DROP FUNCTION IF EXISTS resume_paused_generation_turn(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS pause_generation_turn_owned(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS load_generation_checkpoint(UUID, UUID);
DROP FUNCTION IF EXISTS save_generation_checkpoint(UUID, UUID, TEXT, JSONB);
DROP TABLE IF EXISTS conversation_turn_checkpoints;
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks
    ADD CONSTRAINT tasks_status_check
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'));
DROP INDEX IF EXISTS idx_tasks_user_pending;
CREATE INDEX IF NOT EXISTS idx_tasks_user_pending
    ON tasks(user_id, status)
    WHERE status IN ('pending', 'running');
ALTER TABLE conversation_control_events
    DROP CONSTRAINT IF EXISTS conversation_control_events_type_check;
ALTER TABLE conversation_control_events
    ADD CONSTRAINT conversation_control_events_type_check
    CHECK (event_type IN (
        'cancel', 'approval_result', 'subtask_completed', 'tool_completed'
    ));
