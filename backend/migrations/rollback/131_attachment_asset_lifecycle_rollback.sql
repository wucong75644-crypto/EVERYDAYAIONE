DROP FUNCTION IF EXISTS enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
);
DROP FUNCTION IF EXISTS enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
);
DROP FUNCTION IF EXISTS bind_task_attachments(UUID, UUID, UUID, UUID, UUID);
DROP FUNCTION IF EXISTS current_attachment_parts(UUID, UUID);
DROP FUNCTION IF EXISTS stage_wecom_attachment_v2(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, JSONB, UUID
);
DROP TABLE IF EXISTS task_attachment_refs;
DROP INDEX IF EXISTS idx_attachment_active_set;
ALTER TABLE conversation_attachment_refs
    DROP COLUMN IF EXISTS provider_name,
    DROP COLUMN IF EXISTS canonical_name,
    DROP COLUMN IF EXISTS detected_mime_type,
    DROP COLUMN IF EXISTS detection_source,
    DROP COLUMN IF EXISTS content_sha256,
    DROP COLUMN IF EXISTS attachment_set_id,
    DROP COLUMN IF EXISTS last_referenced_at;
