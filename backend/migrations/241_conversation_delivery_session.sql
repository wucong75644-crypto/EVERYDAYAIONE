-- 241: Conversation Actor 可恢复流式交付会话。
--
-- ReplayCheckpoint 决定模型从哪里继续；本迁移只记录页面交付事实。
-- Redis/WebSocket 仍然负责低延迟投递，PostgreSQL 负责会话、序号和回放。

CREATE TABLE IF NOT EXISTS public.conversation_delivery_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    message_id UUID NOT NULL REFERENCES public.messages(id) ON DELETE CASCADE,
    stream_id UUID NOT NULL,
    execution_token UUID NOT NULL,
    execution_attempt INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'streaming',
    next_seq BIGINT NOT NULL DEFAULT 0,
    snapshot_seq BIGINT NOT NULL DEFAULT 0,
    snapshot_content TEXT NOT NULL DEFAULT '',
    snapshot_blocks JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_delivery_sessions_task_unique UNIQUE (task_id),
    CONSTRAINT conversation_delivery_sessions_stream_unique UNIQUE (stream_id),
    CONSTRAINT conversation_delivery_sessions_status_check
        CHECK (status IN ('streaming', 'paused', 'committed', 'failed', 'cancelled')),
    CONSTRAINT conversation_delivery_sessions_attempt_check
        CHECK (execution_attempt >= 1),
    CONSTRAINT conversation_delivery_sessions_seq_check
        CHECK (next_seq >= 0 AND snapshot_seq >= 0 AND snapshot_seq <= next_seq),
    CONSTRAINT conversation_delivery_sessions_blocks_check
        CHECK (jsonb_typeof(snapshot_blocks) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_conversation_delivery_sessions_active
    ON public.conversation_delivery_sessions(task_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.conversation_delivery_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES public.conversation_delivery_sessions(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
    stream_id UUID NOT NULL,
    execution_attempt INTEGER NOT NULL,
    delivery_seq BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_delivery_events_seq_unique UNIQUE (stream_id, delivery_seq),
    CONSTRAINT conversation_delivery_events_type_check
        CHECK (event_type IN (
            'message_start', 'message_chunk', 'thinking_chunk',
            'content_block_add', 'stream_end'
        )),
    CONSTRAINT conversation_delivery_events_attempt_check
        CHECK (execution_attempt >= 1),
    CONSTRAINT conversation_delivery_events_seq_check
        CHECK (delivery_seq >= 1),
    CONSTRAINT conversation_delivery_events_payload_check
        CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_conversation_delivery_events_replay
    ON public.conversation_delivery_events(stream_id, delivery_seq);

CREATE INDEX IF NOT EXISTS idx_conversation_delivery_events_task
    ON public.conversation_delivery_events(task_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.begin_conversation_delivery_session(
    p_task_id UUID,
    p_execution_token UUID,
    p_execution_attempt INTEGER,
    p_message_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_session conversation_delivery_sessions%ROWTYPE;
    v_stream_id UUID;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL
       OR p_execution_attempt < 1 OR p_message_id IS NULL THEN
        RAISE EXCEPTION 'DELIVERY_SESSION_BEGIN_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.assistant_message_id IS DISTINCT FROM p_message_id THEN
        RAISE EXCEPTION 'DELIVERY_SESSION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token
       OR v_task.execution_attempt IS DISTINCT FROM p_execution_attempt THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_session
      FROM conversation_delivery_sessions
     WHERE task_id = p_task_id
     FOR UPDATE;

    IF FOUND
       AND v_session.execution_token IS NOT DISTINCT FROM p_execution_token
       AND v_session.execution_attempt = p_execution_attempt
       AND v_session.status = 'streaming' THEN
        RETURN jsonb_build_object(
            'outcome', 'existing',
            'session_id', v_session.id,
            'stream_id', v_session.stream_id,
            'execution_attempt', v_session.execution_attempt,
            'next_seq', v_session.next_seq,
            'snapshot_seq', v_session.snapshot_seq,
            'snapshot_content', v_session.snapshot_content,
            'snapshot_blocks', v_session.snapshot_blocks
        );
    END IF;

    IF v_session.id IS NOT NULL THEN
        -- 一个 task 只保留当前 delivery stream；旧 attempt 由 ReplayCheckpoint
        -- 恢复模型，旧交付事件不能再次被页面消费。
        DELETE FROM conversation_delivery_events
         WHERE session_id = v_session.id;
    END IF;

    v_stream_id := uuid_generate_v4();
    INSERT INTO conversation_delivery_sessions(
        task_id, conversation_id, message_id, stream_id,
        execution_token, execution_attempt, status, next_seq,
        snapshot_seq, snapshot_content, snapshot_blocks
    ) VALUES (
        p_task_id, v_task.conversation_id, p_message_id, v_stream_id,
        p_execution_token, p_execution_attempt, 'streaming', 0,
        0, COALESCE(v_task.accumulated_content, ''),
        COALESCE(v_task.accumulated_blocks, '[]'::JSONB)
    )
    ON CONFLICT (task_id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        message_id = EXCLUDED.message_id,
        stream_id = EXCLUDED.stream_id,
        execution_token = EXCLUDED.execution_token,
        execution_attempt = EXCLUDED.execution_attempt,
        status = EXCLUDED.status,
        next_seq = EXCLUDED.next_seq,
        snapshot_seq = EXCLUDED.snapshot_seq,
        snapshot_content = EXCLUDED.snapshot_content,
        snapshot_blocks = EXCLUDED.snapshot_blocks,
        updated_at = NOW()
    RETURNING * INTO v_session;

    RETURN jsonb_build_object(
        'outcome', 'started',
        'session_id', v_session.id,
        'stream_id', v_session.stream_id,
        'execution_attempt', v_session.execution_attempt,
        'next_seq', v_session.next_seq,
        'snapshot_seq', v_session.snapshot_seq,
        'snapshot_content', v_session.snapshot_content,
        'snapshot_blocks', v_session.snapshot_blocks
    );
END;
$$;

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

CREATE OR REPLACE FUNCTION public.save_conversation_delivery_snapshot(
    p_task_id UUID,
    p_execution_token UUID,
    p_content TEXT,
    p_blocks JSONB DEFAULT '[]'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_session conversation_delivery_sessions%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL
       OR p_content IS NULL OR p_blocks IS NULL
       OR jsonb_typeof(p_blocks) <> 'array' THEN
        RAISE EXCEPTION 'DELIVERY_SNAPSHOT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat' THEN
        RAISE EXCEPTION 'DELIVERY_SNAPSHOT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    SELECT * INTO v_session FROM conversation_delivery_sessions
     WHERE task_id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_session.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    UPDATE conversation_delivery_sessions
       SET snapshot_seq = next_seq,
           snapshot_content = p_content,
           snapshot_blocks = p_blocks,
           updated_at = NOW()
     WHERE id = v_session.id;
    RETURN jsonb_build_object(
        'outcome', 'saved', 'stream_id', v_session.stream_id,
        'execution_attempt', v_session.execution_attempt,
        'snapshot_seq', v_session.next_seq
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.read_conversation_delivery_state(
    p_task_id UUID,
    p_user_id UUID,
    p_last_seq BIGINT DEFAULT 0
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_session conversation_delivery_sessions%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR p_user_id IS NULL OR p_last_seq < 0 THEN
        RAISE EXCEPTION 'DELIVERY_STATE_READ_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks
     WHERE id = p_task_id AND user_id = p_user_id;
    IF NOT FOUND OR v_task.type <> 'chat' THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    SELECT * INTO v_session FROM conversation_delivery_sessions
     WHERE task_id = p_task_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'outcome', 'empty', 'task_status', v_task.status,
            'snapshot_content', COALESCE(v_task.accumulated_content, ''),
            'snapshot_blocks', COALESCE(v_task.accumulated_blocks, '[]'::JSONB)
        );
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found',
        'task_status', v_task.status,
        'session_id', v_session.id,
        'stream_id', v_session.stream_id,
        'execution_attempt', v_session.execution_attempt,
        'delivery_status', v_session.status,
        'next_seq', v_session.next_seq,
        'snapshot_seq', v_session.snapshot_seq,
        'snapshot_content', v_session.snapshot_content,
        'snapshot_blocks', v_session.snapshot_blocks,
        'events', COALESCE((
            SELECT jsonb_agg(to_jsonb(event_row) ORDER BY event_row.delivery_seq)
              FROM (
                  SELECT delivery_seq, event_type, payload
                   FROM conversation_delivery_events
                   WHERE session_id = v_session.id
                     AND delivery_seq > GREATEST(p_last_seq, v_session.snapshot_seq)
                   ORDER BY delivery_seq
                   LIMIT 500
              ) event_row
        ), '[]'::JSONB)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.sync_conversation_delivery_status()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
    UPDATE conversation_delivery_sessions
       SET status = CASE NEW.status
           WHEN 'paused' THEN 'paused'
           WHEN 'completed' THEN 'committed'
           WHEN 'failed' THEN 'failed'
           WHEN 'cancelled' THEN 'cancelled'
           ELSE status
       END,
       updated_at = NOW()
     WHERE task_id = NEW.id
       AND NEW.status IN ('paused', 'completed', 'failed', 'cancelled');
    IF NEW.status IN ('completed', 'failed', 'cancelled') THEN
        DELETE FROM conversation_delivery_events
         WHERE task_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tasks_conversation_delivery_status_trigger ON public.tasks;
CREATE TRIGGER tasks_conversation_delivery_status_trigger
AFTER UPDATE OF status ON public.tasks
FOR EACH ROW
EXECUTE FUNCTION public.sync_conversation_delivery_status();

REVOKE ALL ON FUNCTION public.begin_conversation_delivery_session(UUID, UUID, INTEGER, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_conversation_delivery_event(UUID, UUID, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.save_conversation_delivery_snapshot(UUID, UUID, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.read_conversation_delivery_state(UUID, UUID, BIGINT) FROM PUBLIC;

COMMENT ON TABLE public.conversation_delivery_sessions IS
    'Conversation Actor 页面流式交付会话；与 ReplayCheckpoint 分离，支持刷新/重连恢复';
COMMENT ON TABLE public.conversation_delivery_events IS
    'Conversation Actor 页面交付事件；Redis 只做实时通知，事件可按 stream_id/seq 回放';
