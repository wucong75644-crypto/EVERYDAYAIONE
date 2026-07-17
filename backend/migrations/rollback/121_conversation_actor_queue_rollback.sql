-- 回滚 121：仅在 Actor feature flag 关闭且无 Actor task 运行时执行。

DROP FUNCTION IF EXISTS renew_generation_lease(UUID, UUID, INTEGER);
DROP FUNCTION IF EXISTS claim_branch_generation_turn(UUID, INTEGER, INTEGER);
DROP FUNCTION IF EXISTS claim_next_serial_generation_turn(UUID, INTEGER, INTEGER);
DROP FUNCTION IF EXISTS enqueue_generation_turn(JSONB, UUID, UUID, TEXT, JSONB);

DROP INDEX IF EXISTS idx_tasks_actor_active;
DROP INDEX IF EXISTS idx_tasks_actor_expired_lease;
DROP INDEX IF EXISTS idx_tasks_actor_queue;

ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_active_serial_task_id_fkey,
    DROP COLUMN IF EXISTS actor_updated_at,
    DROP COLUMN IF EXISTS active_serial_task_id;

ALTER TABLE tasks
    DROP CONSTRAINT IF EXISTS tasks_delivery_context_object_check,
    DROP CONSTRAINT IF EXISTS tasks_execution_attempt_check,
    DROP COLUMN IF EXISTS terminal_reason,
    DROP COLUMN IF EXISTS delivery_context,
    DROP COLUMN IF EXISTS execution_attempt,
    DROP COLUMN IF EXISTS lease_expires_at,
    DROP COLUMN IF EXISTS execution_token,
    DROP COLUMN IF EXISTS queue_sequence;

DROP SEQUENCE IF EXISTS task_queue_sequence_seq;
