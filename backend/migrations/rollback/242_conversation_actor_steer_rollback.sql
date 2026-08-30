-- 仅在没有未完成 steer 事件时回滚 242。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM conversation_control_events
         WHERE event_type = 'steer'
           AND status = 'pending'
    ) THEN
        RAISE EXCEPTION 'ACTOR_STEER_EVENTS_PENDING';
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS append_conversation_steer(UUID, UUID, UUID, TEXT, JSONB);

ALTER TABLE conversation_control_events
    DROP CONSTRAINT IF EXISTS conversation_control_events_type_check,
    ADD CONSTRAINT conversation_control_events_type_check
        CHECK (event_type IN (
            'cancel', 'pause', 'resume', 'approval_result',
            'subtask_completed', 'tool_completed'
        ));
