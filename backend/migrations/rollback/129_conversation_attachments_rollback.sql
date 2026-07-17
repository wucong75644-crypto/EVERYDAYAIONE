DROP FUNCTION IF EXISTS stage_wecom_attachment(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, UUID
);
DROP TABLE IF EXISTS conversation_attachment_refs;
ALTER TABLE messages
    DROP COLUMN IF EXISTS sender_channel_identity,
    DROP COLUMN IF EXISTS sender_user_id;
