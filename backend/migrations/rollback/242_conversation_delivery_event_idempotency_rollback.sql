DROP FUNCTION IF EXISTS public.append_conversation_delivery_event(UUID, UUID, TEXT, JSONB, UUID);

CREATE OR REPLACE FUNCTION public.append_conversation_delivery_event(
    p_task_id UUID,
    p_execution_token UUID,
    p_event_type TEXT,
    p_payload JSONB DEFAULT '{}'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_session conversation_delivery_sessions%ROWTYPE;
    v_seq BIGINT;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL
       OR p_event_type NOT IN (
           'message_start', 'message_chunk', 'thinking_chunk',
           'content_block_add', 'stream_end'
       )
       OR p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'DELIVERY_EVENT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'DELIVERY_EVENT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    SELECT * INTO v_session
      FROM conversation_delivery_sessions
     WHERE task_id = p_task_id
     FOR UPDATE;
    IF NOT FOUND OR v_session.execution_token IS DISTINCT FROM p_execution_token
       OR v_session.status <> 'streaming' THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    v_seq := v_session.next_seq + 1;
    INSERT INTO conversation_delivery_events(
        session_id, task_id, stream_id, execution_attempt,
        delivery_seq, event_type, payload
    ) VALUES (
        v_session.id, p_task_id, v_session.stream_id,
        v_session.execution_attempt, v_seq, p_event_type, p_payload
    );
    UPDATE conversation_delivery_sessions
       SET next_seq = v_seq, updated_at = NOW()
     WHERE id = v_session.id;
    RETURN jsonb_build_object(
        'outcome', 'appended',
        'session_id', v_session.id,
        'stream_id', v_session.stream_id,
        'execution_attempt', v_session.execution_attempt,
        'delivery_seq', v_seq
    );
END;
$$;

DROP INDEX IF EXISTS public.idx_conversation_delivery_events_stream_event;
ALTER TABLE public.conversation_delivery_events
    DROP COLUMN IF EXISTS event_id;
