-- 仅在没有未完成控制事件时回滚 138。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM conversation_control_events
         WHERE status = 'pending'
    ) THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENTS_PENDING';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS tasks_actor_cancel_control_event_trigger ON tasks;
DROP FUNCTION IF EXISTS create_actor_cancel_control_command();
DROP FUNCTION IF EXISTS acknowledge_conversation_control_command(UUID, UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS read_conversation_control_commands(UUID, UUID, INTEGER);
DROP FUNCTION IF EXISTS append_conversation_control_command(UUID, UUID, UUID, TEXT, TEXT, JSONB);
DROP TABLE IF EXISTS conversation_control_events;
DROP SEQUENCE IF EXISTS conversation_control_event_sequence_seq;
