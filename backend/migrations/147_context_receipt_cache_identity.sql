-- 147: ContextReceipt Epoch、缓存身份与 Provider 用量持久化
-- 依赖 138_unified_conversation_context.sql。

ALTER TABLE conversation_context_receipts
    ADD COLUMN IF NOT EXISTS context_epoch_id TEXT,
    ADD COLUMN IF NOT EXISTS cache_identity JSONB,
    ADD COLUMN IF NOT EXISTS provider_usage JSONB;

ALTER TABLE conversation_context_receipts
    ADD CONSTRAINT conversation_context_receipts_epoch_check
        CHECK (
            context_epoch_id IS NULL
            OR NULLIF(BTRIM(context_epoch_id), '') IS NOT NULL
        ),
    ADD CONSTRAINT conversation_context_receipts_cache_identity_check
        CHECK (
            cache_identity IS NULL
            OR jsonb_typeof(cache_identity) = 'object'
        ),
    ADD CONSTRAINT conversation_context_receipts_provider_usage_check
        CHECK (
            provider_usage IS NULL
            OR (
                jsonb_typeof(provider_usage) = 'object'
                AND COALESCE(
                    (provider_usage->>'prompt_tokens')::BIGINT,
                    -1
                ) >= 0
                AND COALESCE(
                    (provider_usage->>'completion_tokens')::BIGINT,
                    -1
                ) >= 0
                AND COALESCE(
                    (provider_usage->>'cached_tokens')::BIGINT,
                    -1
                ) >= 0
                AND COALESCE(
                    (provider_usage->>'cache_creation_tokens')::BIGINT,
                    -1
                ) >= 0
            )
        );

CREATE INDEX IF NOT EXISTS idx_context_receipts_epoch
    ON conversation_context_receipts(
        conversation_id, context_epoch_id, model_step
    )
    WHERE context_epoch_id IS NOT NULL;

CREATE OR REPLACE FUNCTION commit_generation_turn_with_context_v2(
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
    v_item JSONB;
BEGIN
    IF p_context_receipts IS NULL
       OR jsonb_typeof(p_context_receipts) <> 'array'
       OR jsonb_array_length(p_context_receipts) > 64 THEN
        RAISE EXCEPTION 'ACTOR_CONTEXT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_context_receipts)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR NULLIF(BTRIM(v_item->>'context_epoch_id'), '') IS NULL
           OR jsonb_typeof(COALESCE(
                v_item->'cache_identity', 'null'::JSONB
           )) <> 'object'
           OR jsonb_typeof(COALESCE(
                v_item->'provider_usage', 'null'::JSONB
           )) <> 'object'
           OR COALESCE((
                v_item->'provider_usage'->>'prompt_tokens'
           )::BIGINT, -1) < 0
           OR COALESCE((
                v_item->'provider_usage'->>'completion_tokens'
           )::BIGINT, -1) < 0
           OR COALESCE((
                v_item->'provider_usage'->>'cached_tokens'
           )::BIGINT, -1) < 0
           OR COALESCE((
                v_item->'provider_usage'->>'cache_creation_tokens'
           )::BIGINT, -1) < 0 THEN
            RAISE EXCEPTION 'ACTOR_CONTEXT_RECEIPT_CACHE_INVALID'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;

    SELECT commit_generation_turn(
        p_task_id, p_execution_token, p_output_message_id,
        p_result_content, p_usage, p_credits_cost, p_tool_digest,
        p_data_evidence, p_context_items, p_artifacts,
        p_context_receipts, p_compaction
    ) INTO v_result;

    IF COALESCE(v_result->>'outcome', '') IN (
        'committed', 'already_committed'
    ) THEN
        FOR v_item IN
            SELECT value FROM jsonb_array_elements(p_context_receipts)
        LOOP
            UPDATE conversation_context_receipts
               SET context_epoch_id = v_item->>'context_epoch_id',
                   cache_identity = v_item->'cache_identity',
                   provider_usage = v_item->'provider_usage',
                   provider_tokens = (
                       v_item->'provider_usage'->>'prompt_tokens'
                   )::INTEGER
             WHERE task_id = p_task_id
               AND model_step = (v_item->>'model_step')::INTEGER;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'ACTOR_CONTEXT_RECEIPT_MISSING'
                    USING ERRCODE = 'P0002';
            END IF;
        END LOOP;
    END IF;

    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION commit_generation_turn_with_context_v2(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
) FROM PUBLIC;
DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION commit_generation_turn_with_context_v2(
            UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
            JSONB, JSONB, JSONB, JSONB
        ) TO service_role;
    END IF;
END
$grant$;

COMMENT ON COLUMN conversation_context_receipts.context_epoch_id
    IS '稳定 Prompt 前缀所在的逻辑 Context Epoch';
COMMENT ON COLUMN conversation_context_receipts.cache_identity
    IS '稳定前缀、动态后缀、Tool Schema 与会话路由的无正文哈希';
COMMENT ON COLUMN conversation_context_receipts.provider_usage
    IS '单个 ModelStep 的 prompt/completion/cache Provider Token 用量';
