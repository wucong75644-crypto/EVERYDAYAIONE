-- 回滚 120：仅在应用已停止使用 Turn/revision 字段后执行。

DROP FUNCTION IF EXISTS close_generation_turn(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS bind_generation_turn(UUID, UUID, UUID, UUID, TEXT);

DROP INDEX IF EXISTS idx_tasks_input_message;
DROP INDEX IF EXISTS idx_tasks_conversation_turn;
DROP INDEX IF EXISTS idx_messages_reply_to;
DROP INDEX IF EXISTS idx_messages_conversation_turn;
DROP INDEX IF EXISTS idx_messages_conversation_revision_created;

ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_summary_revision_check,
    DROP CONSTRAINT IF EXISTS conversations_context_revision_check,
    DROP CONSTRAINT IF EXISTS conversations_summary_through_message_id_fkey,
    DROP CONSTRAINT IF EXISTS conversations_last_closed_message_id_fkey,
    DROP COLUMN IF EXISTS summary_through_message_id,
    DROP COLUMN IF EXISTS summary_revision,
    DROP COLUMN IF EXISTS last_closed_message_id,
    DROP COLUMN IF EXISTS context_revision;

ALTER TABLE tasks
    DROP CONSTRAINT IF EXISTS tasks_execution_mode_check,
    DROP CONSTRAINT IF EXISTS tasks_base_context_revision_check,
    DROP CONSTRAINT IF EXISTS tasks_context_through_message_id_fkey,
    DROP CONSTRAINT IF EXISTS tasks_input_message_id_fkey,
    DROP COLUMN IF EXISTS execution_mode,
    DROP COLUMN IF EXISTS context_through_message_id,
    DROP COLUMN IF EXISTS base_context_revision,
    DROP COLUMN IF EXISTS turn_id,
    DROP COLUMN IF EXISTS input_message_id;

ALTER TABLE messages
    DROP CONSTRAINT IF EXISTS messages_message_kind_check,
    DROP CONSTRAINT IF EXISTS messages_context_revision_check,
    DROP CONSTRAINT IF EXISTS messages_reply_to_message_id_fkey,
    DROP COLUMN IF EXISTS message_kind,
    DROP COLUMN IF EXISTS context_revision,
    DROP COLUMN IF EXISTS reply_to_message_id,
    DROP COLUMN IF EXISTS turn_id;
