-- Conversation Actor 跨进程控制事件。
-- 依赖 121/122 的 tasks、conversations 与 fencing 字段。
-- 只保存取消、审批、子任务或外部回调等控制事实，不保存 token 流。

CREATE SEQUENCE IF NOT EXISTS conversation_control_event_sequence_seq AS BIGINT;

CREATE TABLE IF NOT EXISTS conversation_control_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_sequence BIGINT NOT NULL
        DEFAULT nextval('conversation_control_event_sequence_seq'),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    turn_id UUID,
    event_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    applied_execution_token UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ,
    CONSTRAINT conversation_control_events_type_check
        CHECK (event_type IN (
            'cancel', 'approval_result', 'subtask_completed', 'tool_completed'
        )),
    CONSTRAINT conversation_control_events_dedupe_check
        CHECK (length(BTRIM(dedupe_key)) BETWEEN 1 AND 200),
    CONSTRAINT conversation_control_events_payload_object_check
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT conversation_control_events_status_check
        CHECK (status IN ('pending', 'applied', 'ignored')),
    CONSTRAINT conversation_control_events_task_dedupe_unique
        UNIQUE (task_id, dedupe_key)
);

ALTER SEQUENCE conversation_control_event_sequence_seq
    OWNED BY conversation_control_events.event_sequence;

CREATE INDEX IF NOT EXISTS idx_conversation_control_events_pending
    ON conversation_control_events(task_id, event_sequence, id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_conversation_control_events_conversation
    ON conversation_control_events(conversation_id, event_sequence, id);

CREATE OR REPLACE FUNCTION append_conversation_control_command(
    p_conversation_id UUID,
    p_task_id UUID,
    p_turn_id UUID,
    p_event_type TEXT,
    p_dedupe_key TEXT,
    p_payload JSONB DEFAULT '{}'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_event conversation_control_events%ROWTYPE;
    v_inserted_count BIGINT;
BEGIN
    IF p_conversation_id IS NULL
       OR p_task_id IS NULL
       OR p_event_type NOT IN (
           'cancel', 'approval_result', 'subtask_completed', 'tool_completed'
       )
       OR NULLIF(BTRIM(p_dedupe_key), '') IS NULL
       OR p_payload IS NULL
       OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_event_type = 'approval_result' AND v_task.status <> 'running' THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_TASK_NOT_RUNNING'
            USING ERRCODE = '55000';
    END IF;

    INSERT INTO conversation_control_events(
        conversation_id, task_id, turn_id, event_type, dedupe_key, payload
    ) VALUES (
        p_conversation_id, p_task_id, p_turn_id, p_event_type,
        BTRIM(p_dedupe_key), p_payload
    )
    ON CONFLICT (task_id, dedupe_key) DO NOTHING;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;

    SELECT * INTO v_event
      FROM conversation_control_events
     WHERE task_id = p_task_id
       AND dedupe_key = BTRIM(p_dedupe_key);

    RETURN jsonb_build_object(
        'outcome', 'enqueued',
        'event_id', v_event.id,
        'event_sequence', v_event.event_sequence,
        'already_enqueued', v_inserted_count = 0,
        'payload', v_event.payload
    );
END;
$$;

CREATE OR REPLACE FUNCTION read_conversation_control_commands(
    p_task_id UUID,
    p_execution_token UUID,
    p_limit INTEGER DEFAULT 50
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
BEGIN
    IF p_task_id IS NULL
       OR p_execution_token IS NULL
       OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_READ_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat' THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN '[]'::JSONB;
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_READ_OWNERSHIP_LOST'
            USING ERRCODE = '42501';
    END IF;

    RETURN COALESCE(
        (
            SELECT jsonb_agg(to_jsonb(pending_events)
                             ORDER BY pending_events.event_sequence,
                                      pending_events.id)
              FROM (
                  SELECT id, conversation_id, task_id, turn_id,
                         event_type, payload, event_sequence
                    FROM conversation_control_events
                   WHERE task_id = p_task_id
                     AND status = 'pending'
                   ORDER BY event_sequence, id
                   LIMIT p_limit
              ) AS pending_events
        ),
        '[]'::JSONB
    );
END;
$$;

CREATE OR REPLACE FUNCTION acknowledge_conversation_control_command(
    p_event_id UUID,
    p_task_id UUID,
    p_execution_token UUID,
    p_outcome TEXT DEFAULT 'applied'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_updated_count BIGINT;
BEGIN
    IF p_event_id IS NULL
       OR p_task_id IS NULL
       OR p_execution_token IS NULL
       OR p_outcome NOT IN ('applied', 'ignored') THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_ACK_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat' THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    UPDATE conversation_control_events
       SET status = p_outcome,
           applied_execution_token = p_execution_token,
           applied_at = NOW()
     WHERE id = p_event_id
       AND task_id = p_task_id
       AND status = 'pending';
    GET DIAGNOSTICS v_updated_count = ROW_COUNT;

    IF v_updated_count = 0 THEN
        RETURN jsonb_build_object('outcome', 'already_acknowledged');
    END IF;
    RETURN jsonb_build_object('outcome', p_outcome, 'event_id', p_event_id);
END;
$$;

CREATE OR REPLACE FUNCTION create_actor_cancel_control_command()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
    IF NEW.status = 'cancelled'
       AND OLD.status IS DISTINCT FROM NEW.status
       AND NEW.type = 'chat'
       AND (NEW.delivery_context @> '{"actor": true}'::JSONB) THEN
        INSERT INTO conversation_control_events(
            conversation_id, task_id, turn_id, event_type, dedupe_key, payload,
            status, applied_at
        ) VALUES (
            NEW.conversation_id, NEW.id, NEW.turn_id, 'cancel',
            'cancel:' || NEW.id::TEXT,
            jsonb_build_object(
                'reason', COALESCE(NEW.terminal_reason, 'user_cancelled')
            ),
            'applied', NOW()
        )
        ON CONFLICT (task_id, dedupe_key) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tasks_actor_cancel_control_event_trigger ON tasks;
CREATE TRIGGER tasks_actor_cancel_control_event_trigger
AFTER UPDATE OF status ON tasks
FOR EACH ROW
EXECUTE FUNCTION create_actor_cancel_control_command();

REVOKE ALL ON FUNCTION append_conversation_control_command(UUID, UUID, UUID, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION read_conversation_control_commands(UUID, UUID, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION acknowledge_conversation_control_command(UUID, UUID, UUID, TEXT) FROM PUBLIC;

COMMENT ON TABLE conversation_control_events
    IS 'Conversation Actor 跨进程控制事实；不保存模型 token 流';
COMMENT ON FUNCTION read_conversation_control_commands(UUID, UUID, INTEGER)
    IS '仅当前 running task 的 fencing owner 可读取待处理控制事件';
COMMENT ON FUNCTION acknowledge_conversation_control_command(UUID, UUID, UUID, TEXT)
    IS '仅当前 fencing owner 可确认控制事件，重复确认幂等';
