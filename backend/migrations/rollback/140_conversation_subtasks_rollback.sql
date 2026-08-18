DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM conversation_subtask_links WHERE status = 'pending'
    ) THEN
        RAISE EXCEPTION 'ACTOR_SUBTASKS_PENDING';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS tasks_subtask_completion_control_event_trigger ON tasks;
DROP FUNCTION IF EXISTS publish_conversation_subtask_completion();
DROP FUNCTION IF EXISTS register_conversation_subtask(UUID, UUID, TEXT, UUID);
DROP TABLE IF EXISTS conversation_subtask_links;
