-- 136: Conversation Evidence 分级模型视图
-- 依赖 135_conversation_data_evidence.sql。

ALTER TABLE conversation_data_evidence
    ADD COLUMN IF NOT EXISTS model_view JSONB,
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS byte_size BIGINT,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

ALTER TABLE conversation_data_evidence
    DROP CONSTRAINT IF EXISTS conversation_data_evidence_model_view_check,
    ADD CONSTRAINT conversation_data_evidence_model_view_check
        CHECK (model_view IS NULL OR jsonb_typeof(model_view) = 'object'),
    DROP CONSTRAINT IF EXISTS conversation_data_evidence_byte_size_check,
    ADD CONSTRAINT conversation_data_evidence_byte_size_check
        CHECK (byte_size IS NULL OR byte_size >= 0);

CREATE INDEX IF NOT EXISTS idx_conversation_data_evidence_expires
    ON conversation_data_evidence(expires_at)
    WHERE expires_at IS NOT NULL;

CREATE OR REPLACE FUNCTION commit_generation_turn(
    p_task_id UUID, p_execution_token UUID, p_output_message_id UUID,
    p_result_content JSONB, p_usage JSONB,
    p_credits_cost INTEGER, p_tool_digest JSONB,
    p_data_evidence JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_result JSONB;
    v_task tasks%ROWTYPE;
    v_item JSONB;
    v_revision BIGINT;
BEGIN
    IF p_data_evidence IS NULL
       OR jsonb_typeof(p_data_evidence) <> 'array'
       OR jsonb_array_length(p_data_evidence) > 20 THEN
        RAISE EXCEPTION 'ACTOR_DATA_EVIDENCE_INVALID'
            USING ERRCODE = '22023';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_data_evidence)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR pg_column_size(v_item) > 1048576
           OR NULLIF(BTRIM(v_item->>'artifact_id'), '') IS NULL
           OR COALESCE(v_item->>'validation_status', '') <> 'ready'
           OR jsonb_typeof(COALESCE(v_item->'columns', '[]'::JSONB)) <> 'array'
           OR (
                v_item ? 'rows'
                AND v_item->'rows' <> 'null'::JSONB
                AND (
                    jsonb_typeof(v_item->'rows') <> 'array'
                    OR jsonb_array_length(v_item->'rows') > 200
                )
           )
           OR jsonb_typeof(
                COALESCE(v_item->'query_scope', '{}'::JSONB)
              ) <> 'object'
           OR jsonb_typeof(
                COALESCE(v_item->'metric_definitions', '{}'::JSONB)
              ) <> 'object'
           OR jsonb_typeof(
                COALESCE(v_item->'lineage', '{}'::JSONB)
              ) <> 'object'
           OR (
                v_item ? 'model_view'
                AND jsonb_typeof(v_item->'model_view') <> 'object'
           )
           OR (
                v_item ? 'content_hash'
                AND NULLIF(BTRIM(v_item->>'content_hash'), '') IS NULL
           )
           OR (
                v_item ? 'byte_size'
                AND COALESCE((v_item->>'byte_size')::BIGINT, -1) < 0
           ) THEN
            RAISE EXCEPTION 'ACTOR_DATA_EVIDENCE_INVALID'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;

    SELECT commit_generation_turn(
        p_task_id, p_execution_token, p_output_message_id,
        p_result_content, p_usage, p_credits_cost, p_tool_digest
    ) INTO v_result;

    IF COALESCE(v_result->>'outcome', '') NOT IN (
        'committed', 'already_committed'
    ) OR jsonb_array_length(p_data_evidence) = 0 THEN
        RETURN v_result;
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id;
    v_revision := (v_result->>'closed_revision')::BIGINT;
    IF v_task.id IS NULL
       OR v_task.assistant_message_id IS DISTINCT FROM p_output_message_id
       OR v_revision IS NULL THEN
        RAISE EXCEPTION 'ACTOR_DATA_EVIDENCE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_data_evidence)
    LOOP
        INSERT INTO conversation_data_evidence(
            conversation_id, org_id, task_id, source_message_id,
            context_revision, artifact_id, source, columns, rows,
            file_ref, query_scope, metric_definitions, lineage,
            validation_status, model_view, content_hash, byte_size, expires_at
        ) VALUES (
            v_task.conversation_id, v_task.org_id, v_task.id,
            p_output_message_id, v_revision, v_item->>'artifact_id',
            COALESCE(v_item->>'source', ''),
            COALESCE(v_item->'columns', '[]'::JSONB),
            CASE
                WHEN v_item->'rows' = 'null'::JSONB THEN NULL
                ELSE v_item->'rows'
            END,
            CASE
                WHEN v_item->'file_ref' = 'null'::JSONB THEN NULL
                ELSE v_item->'file_ref'
            END,
            COALESCE(v_item->'query_scope', '{}'::JSONB),
            COALESCE(v_item->'metric_definitions', '{}'::JSONB),
            COALESCE(v_item->'lineage', '{}'::JSONB),
            'ready', v_item->'model_view', v_item->>'content_hash',
            (v_item->>'byte_size')::BIGINT,
            (v_item->>'expires_at')::TIMESTAMPTZ
        )
        ON CONFLICT (conversation_id, artifact_id) DO NOTHING;
    END LOOP;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB
) FROM PUBLIC;

COMMENT ON COLUMN conversation_data_evidence.model_view
    IS '按大小分级、可直接进入模型活动工作集的确定性 JSON 视图';
COMMENT ON COLUMN conversation_data_evidence.content_hash
    IS '原始结构化 Evidence 规范序列化后的 SHA-256';
COMMENT ON COLUMN conversation_data_evidence.byte_size
    IS '原始结构化 Evidence 规范序列化后的 UTF-8 字节数';
