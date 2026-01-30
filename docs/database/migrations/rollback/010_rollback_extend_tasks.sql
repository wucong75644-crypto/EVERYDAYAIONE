-- rollback/010_rollback_extend_tasks.sql
-- 回滚任务表扩展
-- 创建日期: 2026-01-30

ALTER TABLE tasks
  DROP COLUMN IF EXISTS external_task_id,
  DROP COLUMN IF EXISTS request_params,
  DROP COLUMN IF EXISTS result,
  DROP COLUMN IF EXISTS fail_code,
  DROP COLUMN IF EXISTS placeholder_message_id,
  DROP COLUMN IF EXISTS last_polled_at,
  DROP COLUMN IF EXISTS client_context,
  DROP COLUMN IF EXISTS kie_url_expires_at,
  DROP COLUMN IF EXISTS version,
  DROP COLUMN IF EXISTS oss_retry_count;

DROP INDEX IF EXISTS idx_tasks_external_id;
DROP INDEX IF EXISTS idx_tasks_status_type;
DROP INDEX IF EXISTS idx_tasks_user_pending;
