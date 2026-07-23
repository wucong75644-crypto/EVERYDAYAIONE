DROP FUNCTION IF EXISTS commit_generation_turn_with_context_v2(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
);

DROP INDEX IF EXISTS idx_context_receipts_epoch;

ALTER TABLE conversation_context_receipts
    DROP CONSTRAINT IF EXISTS
        conversation_context_receipts_provider_usage_check,
    DROP CONSTRAINT IF EXISTS
        conversation_context_receipts_cache_identity_check,
    DROP CONSTRAINT IF EXISTS
        conversation_context_receipts_epoch_check,
    DROP COLUMN IF EXISTS provider_usage,
    DROP COLUMN IF EXISTS cache_identity,
    DROP COLUMN IF EXISTS context_epoch_id;
