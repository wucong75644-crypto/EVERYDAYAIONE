DROP FUNCTION IF EXISTS resolve_wecom_conversation(
    UUID, TEXT, TEXT, TEXT, UUID
);
DROP FUNCTION IF EXISTS claim_legacy_wecom_conversation(
    UUID, TEXT, TEXT, UUID
);
DROP TABLE IF EXISTS conversation_channel_bindings;
ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_scope_owner_check,
    DROP CONSTRAINT IF EXISTS conversations_scope_type_check,
    DROP COLUMN IF EXISTS scope_id,
    DROP COLUMN IF EXISTS scope_type;
ALTER TABLE conversations ALTER COLUMN user_id SET NOT NULL;
