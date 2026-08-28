-- 139: Artifact 存储互斥字段归一化与 Actor 重试耗尽终态闭合
-- 依赖 121_conversation_actor_queue.sql 与 138_unified_conversation_context.sql。

CREATE OR REPLACE FUNCTION normalize_conversation_artifact_storage()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    IF NEW.storage_kind = 'inline' THEN
        NEW.storage_ref := NULL;
    ELSIF NEW.storage_kind IN ('oss', 'message_slice') THEN
        NEW.inline_content := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_normalize_conversation_artifact_storage
    ON conversation_artifacts;
CREATE TRIGGER trg_normalize_conversation_artifact_storage
BEFORE INSERT OR UPDATE OF storage_kind, inline_content, storage_ref
ON conversation_artifacts
FOR EACH ROW
EXECUTE FUNCTION normalize_conversation_artifact_storage();

CREATE OR REPLACE FUNCTION close_exhausted_actor_message()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    IF NEW.status = 'failed'
       AND NEW.terminal_reason = 'lease_attempts_exhausted'
       AND (
            OLD.status IS DISTINCT FROM NEW.status
            OR OLD.terminal_reason IS DISTINCT FROM NEW.terminal_reason
       ) THEN
        UPDATE messages
           SET status = 'failed',
               is_error = TRUE
         WHERE id = NEW.assistant_message_id
           AND status = 'streaming';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_close_exhausted_actor_message ON tasks;
CREATE TRIGGER trg_close_exhausted_actor_message
AFTER UPDATE OF status, terminal_reason ON tasks
FOR EACH ROW
EXECUTE FUNCTION close_exhausted_actor_message();

UPDATE messages AS message
   SET status = 'failed',
       is_error = TRUE
  FROM tasks AS task
 WHERE task.assistant_message_id = message.id
   AND task.status = 'failed'
   AND task.terminal_reason = 'lease_attempts_exhausted'
   AND message.status = 'streaming';
