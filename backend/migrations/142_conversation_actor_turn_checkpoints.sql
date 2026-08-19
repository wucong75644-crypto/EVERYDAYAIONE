-- 142: Conversation Actor 可恢复 turn 检查点与暂停/恢复 RPC。
-- 检查点保存的是最近安全边界的可序列化执行状态，不承诺 token 级续流。

ALTER TABLE conversation_control_events
    DROP CONSTRAINT IF EXISTS conversation_control_events_type_check;
ALTER TABLE conversation_control_events
    ADD CONSTRAINT conversation_control_events_type_check
    CHECK (event_type IN (
        'cancel', 'pause', 'resume', 'approval_result',
        'subtask_completed', 'tool_completed'
    ));

-- tasks 的基础 schema 对 status 有约束；暂停必须成为一等持久状态。
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks
    ADD CONSTRAINT tasks_status_check
    CHECK (status IN ('pending', 'running', 'paused', 'completed', 'failed', 'cancelled'));
DROP INDEX IF EXISTS idx_tasks_user_pending;
CREATE INDEX IF NOT EXISTS idx_tasks_user_pending
    ON tasks(user_id, status)
    WHERE status IN ('pending', 'running', 'paused');

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
    IF p_conversation_id IS NULL OR p_task_id IS NULL
       OR p_event_type NOT IN (
           'cancel', 'pause', 'resume', 'approval_result',
           'subtask_completed', 'tool_completed'
       )
       OR NULLIF(BTRIM(p_dedupe_key), '') IS NULL
       OR p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF p_event_type IN ('approval_result', 'resume')
       AND v_task.status <> 'running' THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_TASK_NOT_RUNNING' USING ERRCODE = '55000';
    END IF;
    INSERT INTO conversation_control_events(
        conversation_id, task_id, turn_id, event_type, dedupe_key, payload
    ) VALUES (
        p_conversation_id, p_task_id, p_turn_id, p_event_type,
        BTRIM(p_dedupe_key), p_payload
    ) ON CONFLICT (task_id, dedupe_key) DO NOTHING;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;
    SELECT * INTO v_event FROM conversation_control_events
     WHERE task_id = p_task_id AND dedupe_key = BTRIM(p_dedupe_key);
    RETURN jsonb_build_object(
        'outcome', 'enqueued', 'event_id', v_event.id,
        'event_sequence', v_event.event_sequence,
        'already_enqueued', v_inserted_count = 0,
        'payload', v_event.payload
    );
END;
$$;

-- API 侧的 OrgScopedDB 会自动注入 p_org_id；保留六参数版本供 Actor
-- 内部 raw DB 调用，同时用七参数重载在边界处校验租户归属。
CREATE OR REPLACE FUNCTION append_conversation_control_command(
    p_conversation_id UUID,
    p_task_id UUID,
    p_turn_id UUID,
    p_event_type TEXT,
    p_dedupe_key TEXT,
    p_payload JSONB,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
BEGIN
    SELECT * INTO v_task
      FROM tasks
     WHERE id = p_task_id;
    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    RETURN append_conversation_control_command(
        p_conversation_id, p_task_id, p_turn_id, p_event_type,
        p_dedupe_key, p_payload
    );
END;
$$;

CREATE TABLE IF NOT EXISTS conversation_turn_checkpoints (
    task_id UUID PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id UUID NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    safe_point TEXT NOT NULL,
    state JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_turn_checkpoints_state_object_check
        CHECK (jsonb_typeof(state) = 'object'),
    CONSTRAINT conversation_turn_checkpoints_status_check
        CHECK (status IN ('ready', 'paused', 'consumed', 'invalid'))
);

CREATE INDEX IF NOT EXISTS idx_conversation_turn_checkpoints_conversation
    ON conversation_turn_checkpoints(conversation_id, updated_at DESC);

CREATE OR REPLACE FUNCTION save_generation_checkpoint(
    p_task_id UUID,
    p_execution_token UUID,
    p_safe_point TEXT,
    p_state JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_version BIGINT;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL
       OR NULLIF(BTRIM(p_safe_point), '') IS NULL
       OR p_state IS NULL OR jsonb_typeof(p_state) <> 'object' THEN
        RAISE EXCEPTION 'ACTOR_CHECKPOINT_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CHECKPOINT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_task.lease_expires_at IS NULL OR v_task.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;

    INSERT INTO conversation_turn_checkpoints(
        task_id, conversation_id, turn_id, version, safe_point, state, status
    ) VALUES (
        p_task_id, v_task.conversation_id, v_task.turn_id,
        1, BTRIM(p_safe_point), p_state, 'ready'
    )
    ON CONFLICT (task_id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        turn_id = EXCLUDED.turn_id,
        version = conversation_turn_checkpoints.version + 1,
        safe_point = EXCLUDED.safe_point,
        state = EXCLUDED.state,
        status = 'ready',
        updated_at = NOW();

    SELECT version INTO v_version
      FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id;
    RETURN jsonb_build_object(
        'outcome', 'saved', 'task_id', p_task_id, 'version', v_version
    );
END;
$$;

CREATE OR REPLACE FUNCTION load_generation_checkpoint(
    p_task_id UUID,
    p_execution_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_checkpoint conversation_turn_checkpoints%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CHECKPOINT_LOAD_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CHECKPOINT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_checkpoint
      FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id
       AND status IN ('ready', 'paused');
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'empty');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'loaded', 'version', v_checkpoint.version,
        'safe_point', v_checkpoint.safe_point, 'state', v_checkpoint.state
    );
END;
$$;

CREATE OR REPLACE FUNCTION pause_generation_turn_owned(
    p_task_id UUID,
    p_execution_token UUID,
    p_reason TEXT DEFAULT 'user_paused'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_snapshot JSONB;
    v_checkpoint conversation_turn_checkpoints%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'paused' THEN
        RETURN jsonb_build_object('outcome', 'already_paused', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_checkpoint
      FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id AND status = 'ready'
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_CHECKPOINT_MISSING' USING ERRCODE = '55000';
    END IF;

    v_snapshot := materialize_actor_cancel_snapshot(p_task_id);
    UPDATE conversation_turn_checkpoints
       SET status = 'paused', updated_at = NOW()
     WHERE task_id = p_task_id AND version = v_checkpoint.version;
    UPDATE conversation_control_events
       SET status = 'applied', applied_execution_token = p_execution_token,
           applied_at = NOW()
     WHERE task_id = p_task_id
       AND dedupe_key = 'pause:' || p_task_id::TEXT
       AND status = 'pending';

    UPDATE tasks
       SET status = 'paused',
           error_message = COALESCE(NULLIF(BTRIM(p_reason), ''), '用户暂停了任务'),
           execution_token = NULL,
           lease_expires_at = NULL,
           terminal_reason = 'user_paused'
     WHERE id = p_task_id;
    UPDATE conversations
       SET active_serial_task_id = NULL, actor_updated_at = NOW()
     WHERE id = v_task.conversation_id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object(
        'outcome', 'paused', 'task_id', p_task_id,
        'checkpoint_version', v_checkpoint.version,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$$;

CREATE OR REPLACE FUNCTION resume_paused_generation_turn(
    p_task_id UUID,
    p_user_id UUID,
    p_org_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_checkpoint conversation_turn_checkpoints%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR p_user_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_RESUME_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_task.conversation_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.user_id IS DISTINCT FROM p_user_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.user_id IS DISTINCT FROM p_user_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_RESUME_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'pending' OR v_task.status = 'running' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_enqueued', 'task_id', p_task_id,
            'conversation_id', v_task.conversation_id,
            'external_task_id', v_task.external_task_id,
            'client_task_id', v_task.client_task_id
        );
    END IF;
    IF v_task.status <> 'paused' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    SELECT * INTO v_checkpoint FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id AND status = 'paused' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_RESUME_CHECKPOINT_MISSING' USING ERRCODE = '55000';
    END IF;

    UPDATE tasks SET status = 'pending', error_message = NULL,
                     terminal_reason = 'resume_requested'
     WHERE id = p_task_id;
    UPDATE conversation_turn_checkpoints
       SET status = 'ready', updated_at = NOW()
     WHERE task_id = p_task_id;
    INSERT INTO conversation_control_events(
        conversation_id, task_id, turn_id, event_type, dedupe_key, payload,
        status, applied_at
    ) VALUES (
        v_task.conversation_id, p_task_id, v_task.turn_id, 'resume',
        'resume:' || p_task_id::TEXT || ':' || v_checkpoint.version,
        jsonb_build_object('user_id', p_user_id), 'applied', NOW()
    ) ON CONFLICT (task_id, dedupe_key) DO NOTHING;

    RETURN jsonb_build_object(
        'outcome', 'enqueued', 'task_id', p_task_id,
        'checkpoint_version', v_checkpoint.version,
        'conversation_id', v_task.conversation_id,
        'assistant_message_id', v_task.assistant_message_id,
        'external_task_id', v_task.external_task_id,
        'client_task_id', v_task.client_task_id
    );
END;
$$;

REVOKE ALL ON FUNCTION save_generation_checkpoint(UUID, UUID, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION load_generation_checkpoint(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION pause_generation_turn_owned(UUID, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION resume_paused_generation_turn(UUID, UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION append_conversation_control_command(
    UUID, UUID, UUID, TEXT, TEXT, JSONB, UUID
) FROM PUBLIC;
