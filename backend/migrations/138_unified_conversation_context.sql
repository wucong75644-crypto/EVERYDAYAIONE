-- 138: 统一 ConversationItem / Artifact / Compaction 持久层
-- 依赖 136_conversation_evidence_model_view.sql 与
-- 137_context_summary_revision_rpc.sql。
-- 保留既有 7/8 参数 commit_generation_turn；统一上下文主链调用 12 参数重载。

CREATE TABLE IF NOT EXISTS conversation_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    org_id UUID,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    tool_call_id TEXT,
    tool_name TEXT NOT NULL DEFAULT '',
    artifact_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    storage_kind TEXT NOT NULL,
    inline_content JSONB,
    storage_ref JSONB,
    model_view JSONB NOT NULL DEFAULT '{}'::JSONB,
    history_view JSONB NOT NULL DEFAULT '{}'::JSONB,
    content_hash TEXT NOT NULL,
    byte_size BIGINT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    sensitivity TEXT NOT NULL DEFAULT 'internal',
    context_revision BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    CONSTRAINT conversation_artifacts_type_check
        CHECK (artifact_type IN (
            'text', 'json', 'table', 'file', 'image', 'error', 'mixed'
        )),
    CONSTRAINT conversation_artifacts_status_check
        CHECK (status IN ('pending', 'ready', 'failed', 'cancelled')),
    CONSTRAINT conversation_artifacts_storage_check
        CHECK (storage_kind IN ('inline', 'oss', 'message_slice')),
    CONSTRAINT conversation_artifacts_content_check
        CHECK (
            (storage_kind = 'inline' AND inline_content IS NOT NULL
                AND storage_ref IS NULL)
            OR
            (storage_kind IN ('oss', 'message_slice')
                AND inline_content IS NULL
                AND jsonb_typeof(storage_ref) = 'object')
        ),
    CONSTRAINT conversation_artifacts_model_view_check
        CHECK (jsonb_typeof(model_view) = 'object'),
    CONSTRAINT conversation_artifacts_history_view_check
        CHECK (jsonb_typeof(history_view) = 'object'),
    CONSTRAINT conversation_artifacts_metadata_check
        CHECK (jsonb_typeof(metadata) = 'object'),
    CONSTRAINT conversation_artifacts_hash_check
        CHECK (NULLIF(BTRIM(content_hash), '') IS NOT NULL),
    CONSTRAINT conversation_artifacts_size_check
        CHECK (byte_size >= 0),
    CONSTRAINT conversation_artifacts_revision_check
        CHECK (context_revision IS NULL OR context_revision > 0),
    CONSTRAINT conversation_artifacts_identity_unique
        UNIQUE (conversation_id, id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_artifacts_revision
    ON conversation_artifacts(
        conversation_id, context_revision DESC, created_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_conversation_artifacts_task
    ON conversation_artifacts(task_id, created_at)
    WHERE task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversation_artifacts_expires
    ON conversation_artifacts(expires_at)
    WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversation_artifacts_content_hash
    ON conversation_artifacts(conversation_id, artifact_type, content_hash);

CREATE TABLE IF NOT EXISTS conversation_context_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    org_id UUID,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    turn_id UUID,
    source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    sequence BIGINT NOT NULL,
    local_sequence INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    group_id UUID,
    payload JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    context_revision BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_context_items_type_check
        CHECK (item_type IN (
            'system', 'user', 'assistant', 'reasoning', 'tool_call',
            'tool_result', 'artifact_ref', 'compaction', 'interrupt'
        )),
    CONSTRAINT conversation_context_items_payload_check
        CHECK (
            jsonb_typeof(payload) = 'object'
            AND pg_column_size(payload) <= 262144
        ),
    CONSTRAINT conversation_context_items_hash_check
        CHECK (NULLIF(BTRIM(content_hash), '') IS NOT NULL),
    CONSTRAINT conversation_context_items_revision_check
        CHECK (context_revision > 0),
    CONSTRAINT conversation_context_items_local_sequence_check
        CHECK (local_sequence BETWEEN 0 AND 999),
    CONSTRAINT conversation_context_items_sequence_unique
        UNIQUE (conversation_id, sequence),
    CONSTRAINT conversation_context_items_task_local_unique
        UNIQUE (task_id, local_sequence)
);

CREATE INDEX IF NOT EXISTS idx_conversation_context_items_revision
    ON conversation_context_items(
        conversation_id, context_revision, local_sequence
    );
CREATE INDEX IF NOT EXISTS idx_conversation_context_items_group
    ON conversation_context_items(conversation_id, group_id)
    WHERE group_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversation_compactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    org_id UUID,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    from_sequence BIGINT NOT NULL,
    through_sequence BIGINT NOT NULL,
    source_hash TEXT NOT NULL,
    summary_payload JSONB NOT NULL,
    summary_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    pass_count INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    context_revision BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_compactions_range_check
        CHECK (from_sequence >= 0 AND through_sequence >= from_sequence),
    CONSTRAINT conversation_compactions_source_hash_check
        CHECK (NULLIF(BTRIM(source_hash), '') IS NOT NULL),
    CONSTRAINT conversation_compactions_summary_check
        CHECK (
            jsonb_typeof(summary_payload) = 'object'
            AND pg_column_size(summary_payload) <= 262144
            AND NULLIF(BTRIM(summary_hash), '') IS NOT NULL
        ),
    CONSTRAINT conversation_compactions_tokens_check
        CHECK (
            pass_count BETWEEN 1 AND 2
            AND input_tokens >= 0
            AND output_tokens >= 0
        ),
    CONSTRAINT conversation_compactions_status_check
        CHECK (status IN ('ready', 'failed')),
    CONSTRAINT conversation_compactions_revision_check
        CHECK (context_revision > 0),
    CONSTRAINT conversation_compactions_source_unique
        UNIQUE (conversation_id, source_hash)
);

CREATE INDEX IF NOT EXISTS idx_conversation_compactions_latest
    ON conversation_compactions(
        conversation_id, through_sequence DESC, created_at DESC
    ) WHERE status = 'ready';

CREATE TABLE IF NOT EXISTS conversation_context_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    org_id UUID,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    model_step INTEGER NOT NULL,
    base_revision BIGINT NOT NULL,
    plan_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    block_refs JSONB NOT NULL,
    estimated_tokens INTEGER NOT NULL,
    provider_tokens INTEGER,
    trimmed_refs JSONB NOT NULL DEFAULT '[]'::JSONB,
    compaction_id UUID REFERENCES conversation_compactions(id)
        ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_context_receipts_step_check
        CHECK (model_step >= 0),
    CONSTRAINT conversation_context_receipts_revision_check
        CHECK (base_revision >= 0),
    CONSTRAINT conversation_context_receipts_hash_check
        CHECK (NULLIF(BTRIM(plan_hash), '') IS NOT NULL),
    CONSTRAINT conversation_context_receipts_refs_check
        CHECK (
            jsonb_typeof(block_refs) = 'array'
            AND jsonb_typeof(trimmed_refs) = 'array'
        ),
    CONSTRAINT conversation_context_receipts_tokens_check
        CHECK (
            estimated_tokens >= 0
            AND (provider_tokens IS NULL OR provider_tokens >= 0)
        ),
    CONSTRAINT conversation_context_receipts_task_step_unique
        UNIQUE (task_id, model_step)
);

CREATE INDEX IF NOT EXISTS idx_conversation_context_receipts_revision
    ON conversation_context_receipts(
        conversation_id, base_revision DESC, created_at DESC
    );

ALTER TABLE conversation_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_context_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_compactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_context_receipts ENABLE ROW LEVEL SECURITY;

DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON conversation_artifacts TO service_role;
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON conversation_context_items TO service_role;
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON conversation_compactions TO service_role;
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON conversation_context_receipts TO service_role;
    END IF;
END
$grant$;

CREATE OR REPLACE FUNCTION commit_generation_turn(
    p_task_id UUID, p_execution_token UUID, p_output_message_id UUID,
    p_result_content JSONB, p_usage JSONB,
    p_credits_cost INTEGER, p_tool_digest JSONB,
    p_data_evidence JSONB, p_context_items JSONB,
    p_artifacts JSONB, p_context_receipts JSONB,
    p_compaction JSONB
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
    v_compaction_id UUID;
BEGIN
    IF p_context_items IS NULL
       OR jsonb_typeof(p_context_items) <> 'array'
       OR jsonb_array_length(p_context_items) > 200
       OR p_artifacts IS NULL
       OR jsonb_typeof(p_artifacts) <> 'array'
       OR jsonb_array_length(p_artifacts) > 100
       OR p_context_receipts IS NULL
       OR jsonb_typeof(p_context_receipts) <> 'array'
       OR jsonb_array_length(p_context_receipts) > 64
       OR (
            p_compaction IS NOT NULL
            AND jsonb_typeof(p_compaction) <> 'object'
       ) THEN
        RAISE EXCEPTION 'ACTOR_CONTEXT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_context_items)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR pg_column_size(v_item) > 262144
           OR COALESCE((v_item->>'local_sequence')::INTEGER, -1)
                NOT BETWEEN 0 AND 999
           OR COALESCE(v_item->>'item_type', '') NOT IN (
                'system', 'user', 'assistant', 'reasoning', 'tool_call',
                'tool_result', 'artifact_ref', 'compaction', 'interrupt'
           )
           OR jsonb_typeof(COALESCE(v_item->'payload', 'null'::JSONB))
                <> 'object'
           OR NULLIF(BTRIM(v_item->>'source_message_id'), '') IS NULL
           OR NULLIF(BTRIM(v_item->>'content_hash'), '') IS NULL THEN
            RAISE EXCEPTION 'ACTOR_CONTEXT_ITEM_INVALID'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_artifacts)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR pg_column_size(v_item) > 524288
           OR COALESCE(v_item->>'artifact_type', '') NOT IN (
                'text', 'json', 'table', 'file', 'image', 'error', 'mixed'
           )
           OR COALESCE(v_item->>'storage_kind', '') NOT IN (
                'inline', 'oss'
           )
           OR NULLIF(BTRIM(v_item->>'content_hash'), '') IS NULL
           OR COALESCE((v_item->>'byte_size')::BIGINT, -1) < 0
           OR jsonb_typeof(COALESCE(v_item->'model_view', '{}'::JSONB))
                <> 'object'
           OR jsonb_typeof(COALESCE(v_item->'history_view', '{}'::JSONB))
                <> 'object'
           OR jsonb_typeof(COALESCE(v_item->'metadata', '{}'::JSONB))
                <> 'object'
           OR (
                v_item->>'storage_kind' = 'inline'
                AND (
                    NOT (v_item ? 'inline_content')
                    OR pg_column_size(v_item->'inline_content') > 65536
                )
           )
           OR (
                v_item->>'storage_kind' = 'oss'
                AND jsonb_typeof(COALESCE(
                    v_item->'storage_ref', 'null'::JSONB
                )) <> 'object'
           ) THEN
            RAISE EXCEPTION 'ACTOR_ARTIFACT_INVALID'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_context_receipts)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR pg_column_size(v_item) > 262144
           OR COALESCE((v_item->>'model_step')::INTEGER, -1) < 0
           OR COALESCE((v_item->>'base_revision')::BIGINT, -1) < 0
           OR NULLIF(BTRIM(v_item->>'plan_hash'), '') IS NULL
           OR NULLIF(BTRIM(v_item->>'model'), '') IS NULL
           OR jsonb_typeof(COALESCE(v_item->'block_refs', 'null'::JSONB))
                <> 'array'
           OR COALESCE((v_item->>'estimated_tokens')::INTEGER, -1) < 0
           OR jsonb_typeof(COALESCE(
                v_item->'trimmed_refs', '[]'::JSONB
           )) <> 'array' THEN
            RAISE EXCEPTION 'ACTOR_CONTEXT_RECEIPT_INVALID'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;

    SELECT commit_generation_turn(
        p_task_id, p_execution_token, p_output_message_id,
        p_result_content, p_usage, p_credits_cost, p_tool_digest,
        p_data_evidence
    ) INTO v_result;

    IF COALESCE(v_result->>'outcome', '') NOT IN (
        'committed', 'already_committed'
    ) THEN
        RETURN v_result;
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id;
    v_revision := (v_result->>'closed_revision')::BIGINT;
    IF v_task.id IS NULL
       OR v_task.assistant_message_id IS DISTINCT FROM p_output_message_id
       OR v_revision IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CONTEXT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_artifacts)
    LOOP
        INSERT INTO conversation_artifacts(
            id, conversation_id, org_id, task_id, source_message_id,
            tool_call_id, tool_name, artifact_type, status, storage_kind,
            inline_content, storage_ref, model_view, history_view,
            content_hash, byte_size, metadata, sensitivity,
            context_revision, expires_at
        ) VALUES (
            COALESCE(NULLIF(v_item->>'id', '')::UUID, gen_random_uuid()),
            v_task.conversation_id, v_task.org_id, v_task.id,
            p_output_message_id, v_item->>'tool_call_id',
            COALESCE(v_item->>'tool_name', ''),
            v_item->>'artifact_type', 'ready', v_item->>'storage_kind',
            v_item->'inline_content', v_item->'storage_ref',
            COALESCE(v_item->'model_view', '{}'::JSONB),
            COALESCE(v_item->'history_view', '{}'::JSONB),
            v_item->>'content_hash', (v_item->>'byte_size')::BIGINT,
            COALESCE(v_item->'metadata', '{}'::JSONB),
            COALESCE(v_item->>'sensitivity', 'internal'),
            v_revision, (v_item->>'expires_at')::TIMESTAMPTZ
        )
        ON CONFLICT (id) DO NOTHING;
    END LOOP;

    IF p_compaction IS NOT NULL THEN
        INSERT INTO conversation_compactions(
            id, conversation_id, org_id, task_id, from_sequence,
            through_sequence, source_hash, summary_payload, summary_hash,
            model, prompt_version, pass_count, input_tokens,
            output_tokens, status, context_revision
        ) VALUES (
            COALESCE(NULLIF(p_compaction->>'id', '')::UUID, gen_random_uuid()),
            v_task.conversation_id, v_task.org_id, v_task.id,
            (p_compaction->>'from_sequence')::BIGINT,
            (p_compaction->>'through_sequence')::BIGINT,
            p_compaction->>'source_hash', p_compaction->'summary_payload',
            p_compaction->>'summary_hash', p_compaction->>'model',
            p_compaction->>'prompt_version',
            (p_compaction->>'pass_count')::INTEGER,
            (p_compaction->>'input_tokens')::INTEGER,
            (p_compaction->>'output_tokens')::INTEGER,
            'ready', v_revision
        )
        ON CONFLICT (conversation_id, source_hash)
        DO UPDATE SET source_hash = EXCLUDED.source_hash
        RETURNING id INTO v_compaction_id;
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_context_items)
    LOOP
        IF NULLIF(v_item->>'source_message_id', '')::UUID NOT IN (
            v_task.input_message_id, p_output_message_id
        ) THEN
            RAISE EXCEPTION 'ACTOR_CONTEXT_MESSAGE_SCOPE_MISMATCH'
                USING ERRCODE = '42501';
        END IF;
        INSERT INTO conversation_context_items(
            id, conversation_id, org_id, task_id, turn_id,
            source_message_id, sequence, local_sequence, item_type,
            group_id, payload, content_hash, context_revision
        ) VALUES (
            COALESCE(NULLIF(v_item->>'id', '')::UUID, gen_random_uuid()),
            v_task.conversation_id, v_task.org_id, v_task.id,
            v_task.turn_id, (v_item->>'source_message_id')::UUID,
            v_revision * 1000
                + (v_item->>'local_sequence')::INTEGER,
            (v_item->>'local_sequence')::INTEGER,
            v_item->>'item_type', NULLIF(v_item->>'group_id', '')::UUID,
            v_item->'payload', v_item->>'content_hash', v_revision
        )
        ON CONFLICT (task_id, local_sequence) DO NOTHING;
    END LOOP;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_context_receipts)
    LOOP
        INSERT INTO conversation_context_receipts(
            conversation_id, org_id, task_id, model_step, base_revision,
            plan_hash, model, block_refs, estimated_tokens,
            provider_tokens, trimmed_refs, compaction_id
        ) VALUES (
            v_task.conversation_id, v_task.org_id, v_task.id,
            (v_item->>'model_step')::INTEGER,
            (v_item->>'base_revision')::BIGINT, v_item->>'plan_hash',
            v_item->>'model', v_item->'block_refs',
            (v_item->>'estimated_tokens')::INTEGER,
            (v_item->>'provider_tokens')::INTEGER,
            COALESCE(v_item->'trimmed_refs', '[]'::JSONB),
            COALESCE(
                NULLIF(v_item->>'compaction_id', '')::UUID,
                v_compaction_id
            )
        )
        ON CONFLICT (task_id, model_step) DO NOTHING;
    END LOOP;

    RETURN v_result || jsonb_build_object(
        'context_items', jsonb_array_length(p_context_items),
        'artifacts', jsonb_array_length(p_artifacts),
        'context_receipts', jsonb_array_length(p_context_receipts),
        'compaction_id', v_compaction_id
    );
END;
$$;

REVOKE ALL ON FUNCTION commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
) FROM PUBLIC;
DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION commit_generation_turn(
            UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
            JSONB, JSONB, JSONB, JSONB
        ) TO service_role;
    END IF;
END
$grant$;

COMMENT ON TABLE conversation_artifacts
    IS '所有工具和执行器完整结果的租户隔离持久事实与有界模型视图';
COMMENT ON TABLE conversation_context_items
    IS '按会话 revision 和局部序号排序的统一模型 ConversationItem 事实流';
COMMENT ON TABLE conversation_compactions
    IS '不删除原始事实的结构化上下文压缩结果';
COMMENT ON TABLE conversation_context_receipts
    IS '每次 Provider 请求实际消费的 ContextPlan 回执';
COMMENT ON FUNCTION commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
) IS '在既有 Actor fencing 原子终态内幂等提交 ContextItem、Artifact、Receipt 与 Compaction';
