-- 242: 页面交付事件使用稳定 event_id，允许 RPC 重试而不重复生成事件。
--
-- Redis/WebSocket 仍然是低延迟投递；PostgreSQL 事件是刷新和断线恢复的真源。

ALTER TABLE public.conversation_delivery_events
    ADD COLUMN IF NOT EXISTS event_id UUID;

UPDATE public.conversation_delivery_events
   SET event_id = uuid_generate_v4()
 WHERE event_id IS NULL;

ALTER TABLE public.conversation_delivery_events
    ALTER COLUMN event_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    idx_conversation_delivery_events_stream_event
    ON public.conversation_delivery_events(stream_id, event_id);

-- 保留旧 RPC 签名供已部署的兼容代码使用；新代码传入稳定 event_id。
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
BEGIN
    RETURN public.append_conversation_delivery_event(
        p_task_id,
        p_execution_token,
        p_event_type,
        p_payload,
        uuid_generate_v4()
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.append_conversation_delivery_event(
    p_task_id UUID,
    p_execution_token UUID,
    p_event_type TEXT,
    p_payload JSONB,
    p_event_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_session conversation_delivery_sessions%ROWTYPE;
    v_event conversation_delivery_events%ROWTYPE;
    v_seq BIGINT;
    v_inserted BOOLEAN;
    v_row_count BIGINT;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL
       OR p_event_id IS NULL
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
        event_id, delivery_seq, event_type, payload
    ) VALUES (
        v_session.id, p_task_id, v_session.stream_id, v_session.execution_attempt,
        p_event_id, v_seq, p_event_type, p_payload
    )
    ON CONFLICT (stream_id, event_id) DO NOTHING;

    GET DIAGNOSTICS v_row_count = ROW_COUNT;
    v_inserted := v_row_count = 1;
    IF v_inserted THEN
        UPDATE conversation_delivery_sessions
           SET next_seq = v_seq, updated_at = NOW()
         WHERE id = v_session.id;
    ELSE
        SELECT * INTO v_event
          FROM conversation_delivery_events
         WHERE stream_id = v_session.stream_id AND event_id = p_event_id;
        IF v_event.event_type IS DISTINCT FROM p_event_type
           OR v_event.payload IS DISTINCT FROM p_payload THEN
            RAISE EXCEPTION 'DELIVERY_EVENT_ID_REUSE'
                USING ERRCODE = '23505';
        END IF;
        v_seq := v_event.delivery_seq;
    END IF;

    RETURN jsonb_build_object(
        'outcome', 'appended',
        'session_id', v_session.id,
        'stream_id', v_session.stream_id,
        'execution_attempt', v_session.execution_attempt,
        'delivery_seq', v_seq
    );
END;
$$;

REVOKE ALL ON FUNCTION public.append_conversation_delivery_event(UUID, UUID, TEXT, JSONB, UUID) FROM PUBLIC;

COMMENT ON COLUMN public.conversation_delivery_events.event_id IS
    '稳定交付事件 ID；RPC 重试必须复用该 ID，避免重复生成 delivery event';
