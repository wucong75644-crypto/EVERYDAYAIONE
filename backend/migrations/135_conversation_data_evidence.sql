-- 135: Conversation Actor 跨 Turn 数据证据
-- 依赖 122_conversation_actor_terminal.sql。
-- 保留原 7 参数 commit_generation_turn；Actor 新链路调用 8 参数重载。

CREATE TABLE IF NOT EXISTS conversation_data_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    org_id UUID,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    source_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    context_revision BIGINT NOT NULL,
    artifact_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    columns JSONB NOT NULL DEFAULT '[]'::JSONB,
    rows JSONB,
    file_ref JSONB,
    query_scope JSONB NOT NULL DEFAULT '{}'::JSONB,
    metric_definitions JSONB NOT NULL DEFAULT '{}'::JSONB,
    lineage JSONB NOT NULL DEFAULT '{}'::JSONB,
    validation_status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_data_evidence_artifact_unique
        UNIQUE (conversation_id, artifact_id),
    CONSTRAINT conversation_data_evidence_revision_check
        CHECK (context_revision > 0),
    CONSTRAINT conversation_data_evidence_columns_check
        CHECK (jsonb_typeof(columns) = 'array'),
    CONSTRAINT conversation_data_evidence_rows_check
        CHECK (rows IS NULL OR jsonb_typeof(rows) = 'array'),
    CONSTRAINT conversation_data_evidence_file_check
        CHECK (file_ref IS NULL OR jsonb_typeof(file_ref) = 'object'),
    CONSTRAINT conversation_data_evidence_scope_check
        CHECK (jsonb_typeof(query_scope) = 'object'),
    CONSTRAINT conversation_data_evidence_metrics_check
        CHECK (jsonb_typeof(metric_definitions) = 'object'),
    CONSTRAINT conversation_data_evidence_lineage_check
        CHECK (jsonb_typeof(lineage) = 'object'),
    CONSTRAINT conversation_data_evidence_status_check
        CHECK (validation_status = 'ready')
);

CREATE INDEX IF NOT EXISTS idx_conversation_data_evidence_revision
    ON conversation_data_evidence(
        conversation_id, context_revision DESC, created_at DESC
    );

ALTER TABLE conversation_data_evidence ENABLE ROW LEVEL SECURITY;

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
              ) <> 'object' THEN
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
            validation_status
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
            'ready'
        )
        ON CONFLICT (conversation_id, artifact_id) DO NOTHING;
    END LOOP;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB
) FROM PUBLIC;

COMMENT ON TABLE conversation_data_evidence
    IS 'Conversation Actor 在 Turn 原子终态内提交的可复算业务数据证据';
COMMENT ON FUNCTION commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB
) IS '复用原 Actor 原子终态，并按 closed revision 幂等提交跨 Turn 数据证据';
