-- 239: 对齐生产已有的 Conversation Actor checkpoint / pause / resume 契约。
--
-- 生产数据库已经存在 conversation_turn_checkpoints 及其 RPC；本迁移必须
-- 在生产上幂等执行，不能创建另一套 replay checkpoint 表，也不能删除
-- 或重命名生产已有对象。旧的 235-238 草稿迁移不属于发布范围。

ALTER TABLE public.tasks
    DROP CONSTRAINT IF EXISTS tasks_status_check,
    ADD CONSTRAINT tasks_status_check
        CHECK (status IN ('pending', 'running', 'paused', 'completed', 'failed', 'cancelled'));

ALTER TABLE public.conversation_control_events
    DROP CONSTRAINT IF EXISTS conversation_control_events_type_check,
    ADD CONSTRAINT conversation_control_events_type_check
        CHECK (event_type IN (
            'cancel', 'pause', 'resume', 'approval_result',
            'subtask_completed', 'tool_completed'
        ));

CREATE TABLE IF NOT EXISTS public.conversation_turn_checkpoints (
    task_id UUID PRIMARY KEY REFERENCES public.tasks(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
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
    ON public.conversation_turn_checkpoints(conversation_id, updated_at DESC);

CREATE OR REPLACE FUNCTION public.append_conversation_control_command(
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
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF p_event_type IN ('approval_result', 'pause', 'cancel')
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

CREATE OR REPLACE FUNCTION public.append_conversation_control_command(
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
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id;
    SELECT * INTO v_conversation FROM conversations WHERE id = p_conversation_id;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_CONTROL_EVENT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    RETURN public.append_conversation_control_command(
        p_conversation_id, p_task_id, p_turn_id, p_event_type,
        p_dedupe_key, p_payload
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.materialize_actor_cancel_snapshot(p_task_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;
    v_blocks JSONB;
    v_content JSONB;
    v_block_text TEXT := '';
    v_remaining TEXT := '';
    v_now TIMESTAMPTZ := NOW();
BEGIN
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL OR v_task.assistant_message_id IS NULL THEN
        RETURN jsonb_build_object('saved', FALSE, 'reason', 'message_missing');
    END IF;
    SELECT * INTO v_message FROM messages
     WHERE id = v_task.assistant_message_id FOR UPDATE;
    IF v_message.id IS NULL THEN
        RETURN jsonb_build_object('saved', FALSE, 'reason', 'message_missing');
    END IF;
    IF v_message.status::TEXT = 'completed' THEN
        RETURN jsonb_build_object('saved', FALSE, 'reason', 'already_completed');
    END IF;

    v_blocks := CASE
        WHEN jsonb_typeof(COALESCE(v_task.accumulated_blocks, '[]'::JSONB)) = 'array'
            THEN COALESCE(v_task.accumulated_blocks, '[]'::JSONB)
        ELSE '[]'::JSONB
    END;
    IF jsonb_array_length(v_blocks) = 0
       AND COALESCE(v_task.accumulated_content, '') = ''
       AND jsonb_typeof(COALESCE(v_message.content, '[]'::JSONB)) = 'array' THEN
        v_blocks := COALESCE(v_message.content, '[]'::JSONB);
    END IF;

    SELECT COALESCE(
        string_agg(COALESCE(item.value->>'text', ''), '' ORDER BY item.ordinality), ''
    ) INTO v_block_text
      FROM jsonb_array_elements(v_blocks) WITH ORDINALITY AS item(value, ordinality)
     WHERE jsonb_typeof(item.value) = 'object' AND item.value->>'type' = 'text';

    IF COALESCE(v_task.accumulated_content, '') <> ''
       AND LEFT(v_task.accumulated_content, LENGTH(v_block_text)) = v_block_text THEN
        v_remaining := SUBSTRING(v_task.accumulated_content FROM LENGTH(v_block_text) + 1);
        IF BTRIM(v_remaining) <> '' THEN
            v_blocks := v_blocks || jsonb_build_array(
                jsonb_build_object('type', 'text', 'text', v_remaining)
            );
        END IF;
    ELSIF jsonb_array_length(v_blocks) = 0
          AND BTRIM(COALESCE(v_task.accumulated_content, '')) <> '' THEN
        v_blocks := jsonb_build_array(
            jsonb_build_object('type', 'text', 'text', v_task.accumulated_content)
        );
    END IF;

    SELECT COALESCE(jsonb_agg(
        CASE
            WHEN item.value->>'type' = 'tool_step'
             AND item.value->>'status' = 'running'
            THEN item.value || jsonb_build_object('status', 'cancelled', 'cancelled_at', v_now)
            ELSE item.value
        END ORDER BY item.ordinality
    ), '[]'::JSONB) INTO v_content
      FROM jsonb_array_elements(v_blocks) WITH ORDINALITY AS item(value, ordinality);

    IF NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(v_content) AS item(value)
         WHERE item.value->>'type' = 'interrupt_marker'
    ) THEN
        v_content := v_content || jsonb_build_array(
            jsonb_build_object(
                'type', 'interrupt_marker', 'interrupted_at', v_now,
                'reason', 'user_cancel'
            )
        );
    END IF;

    UPDATE messages SET content = v_content, status = 'interrupted', is_error = FALSE
     WHERE id = v_message.id AND status::TEXT <> 'completed';
    RETURN jsonb_build_object(
        'saved', TRUE, 'message_id', v_message.id, 'content', v_content
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.cancel_generation_turn_owned(
    p_task_id UUID, p_execution_token UUID, p_reason TEXT DEFAULT 'user_cancelled'
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
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'already_cancelled', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_task.conversation_id FOR UPDATE;
    IF v_conversation.id IS NULL OR v_conversation.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    v_snapshot := public.materialize_actor_cancel_snapshot(p_task_id);
    UPDATE tasks SET status = 'cancelled',
        error_message = COALESCE(NULLIF(BTRIM(p_reason), ''), '用户取消了任务'),
        completed_at = NOW(), execution_token = NULL, lease_expires_at = NULL,
        terminal_reason = 'user_cancelled'
     WHERE id = p_task_id;
    UPDATE conversations SET active_serial_task_id = NULL, actor_updated_at = NOW()
     WHERE id = v_conversation.id AND active_serial_task_id = p_task_id;
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'task_id', p_task_id,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.materialize_actor_pause_snapshot(p_task_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_snapshot JSONB;
    v_content JSONB;
BEGIN
    v_snapshot := public.materialize_actor_cancel_snapshot(p_task_id);
    IF COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE) IS NOT TRUE THEN
        RETURN v_snapshot;
    END IF;

    SELECT COALESCE(jsonb_agg(
        CASE
            WHEN item.value->>'type' = 'interrupt_marker'
            THEN item.value || jsonb_build_object('reason', 'user_pause')
            ELSE item.value
        END ORDER BY item.ordinality
    ), '[]'::JSONB) INTO v_content
      FROM jsonb_array_elements(COALESCE(v_snapshot->'content', '[]'::JSONB))
           WITH ORDINALITY AS item(value, ordinality);
    UPDATE messages SET content = v_content
     WHERE id = (v_snapshot->>'message_id')::UUID;
    RETURN v_snapshot || jsonb_build_object('content', v_content);
END;
$$;

-- 生产 cancel_generation_turn 只接受 pending/running；paused 是可恢复态，
-- 需要单独的最终取消入口，避免“暂停后取消”被错误当作 terminal 而不落终态。
CREATE OR REPLACE FUNCTION public.cancel_paused_generation_turn(
    p_task_id UUID, p_user_id UUID, p_org_id UUID DEFAULT NULL
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
BEGIN
    IF p_task_id IS NULL OR p_user_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_PAUSED_ARGUMENT_INVALID' USING ERRCODE = '22023';
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
        RAISE EXCEPTION 'ACTOR_CANCEL_PAUSED_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'already_cancelled', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'paused' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;

    v_snapshot := public.materialize_actor_cancel_snapshot(p_task_id);
    UPDATE tasks SET status = 'cancelled', error_message = '用户取消了任务',
        completed_at = NOW(), execution_token = NULL, lease_expires_at = NULL,
        terminal_reason = 'user_cancelled'
     WHERE id = p_task_id;
    UPDATE conversations SET active_serial_task_id = NULL, actor_updated_at = NOW()
     WHERE id = v_conversation.id AND active_serial_task_id = p_task_id;
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'task_id', p_task_id,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.save_generation_checkpoint(
    p_task_id UUID, p_execution_token UUID, p_safe_point TEXT, p_state JSONB
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
    ) ON CONFLICT (task_id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        turn_id = EXCLUDED.turn_id,
        version = conversation_turn_checkpoints.version + 1,
        safe_point = EXCLUDED.safe_point,
        state = EXCLUDED.state,
        status = 'ready', updated_at = NOW();

    SELECT version INTO v_version FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id;
    RETURN jsonb_build_object('outcome', 'saved', 'task_id', p_task_id, 'version', v_version);
END;
$$;

CREATE OR REPLACE FUNCTION public.load_generation_checkpoint(
    p_task_id UUID, p_execution_token UUID
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
    SELECT * INTO v_checkpoint FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id AND status IN ('ready', 'paused');
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'empty');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'loaded', 'version', v_checkpoint.version,
        'safe_point', v_checkpoint.safe_point, 'state', v_checkpoint.state
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.pause_generation_turn_owned(
    p_task_id UUID, p_execution_token UUID, p_reason TEXT DEFAULT 'user_paused'
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
    v_output_context_revision BIGINT;
    v_closed_revision BIGINT;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_task.conversation_id FOR UPDATE;
    IF NOT FOUND OR v_conversation.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
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
    SELECT * INTO v_checkpoint FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id AND status = 'ready' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_CHECKPOINT_MISSING' USING ERRCODE = '55000';
    END IF;

    v_snapshot := public.materialize_actor_pause_snapshot(p_task_id);
    SELECT context_revision INTO v_output_context_revision FROM messages
     WHERE id = v_task.assistant_message_id FOR UPDATE;
    IF v_output_context_revision IS NULL THEN
        v_closed_revision := v_conversation.context_revision + 1;
        UPDATE messages SET context_revision = v_closed_revision, message_kind = 'conversation'
         WHERE id = v_task.input_message_id
           AND conversation_id = v_task.conversation_id AND context_revision IS NULL;
        UPDATE messages SET context_revision = v_closed_revision, message_kind = 'conversation'
         WHERE id = v_task.assistant_message_id
           AND conversation_id = v_task.conversation_id AND context_revision IS NULL;
        UPDATE conversations SET context_revision = v_closed_revision,
            last_closed_message_id = v_task.assistant_message_id, actor_updated_at = NOW()
         WHERE id = v_conversation.id;
    ELSE
        v_closed_revision := v_output_context_revision;
    END IF;

    UPDATE conversation_turn_checkpoints SET status = 'paused', updated_at = NOW()
     WHERE task_id = p_task_id AND version = v_checkpoint.version;
    UPDATE conversation_control_events SET status = 'applied',
        applied_execution_token = p_execution_token, applied_at = NOW()
     WHERE task_id = p_task_id AND dedupe_key = 'pause:' || p_task_id::TEXT
       AND status = 'pending';
    UPDATE tasks SET status = 'paused',
        error_message = COALESCE(NULLIF(BTRIM(p_reason), ''), '用户暂停了任务'),
        execution_token = NULL, lease_expires_at = NULL, terminal_reason = 'user_paused'
     WHERE id = p_task_id;
    UPDATE conversations SET active_serial_task_id = NULL, actor_updated_at = NOW()
     WHERE id = v_task.conversation_id AND active_serial_task_id = p_task_id;
    RETURN jsonb_build_object(
        'outcome', 'paused', 'task_id', p_task_id,
        'checkpoint_version', v_checkpoint.version,
        'closed_revision', v_closed_revision,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.resume_paused_generation_turn(
    p_task_id UUID, p_user_id UUID, p_org_id UUID DEFAULT NULL
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
    SELECT * INTO v_conversation FROM conversations WHERE id = v_task.conversation_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.user_id IS DISTINCT FROM p_user_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.user_id IS DISTINCT FROM p_user_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_RESUME_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status IN ('pending', 'running') THEN
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
        terminal_reason = 'resume_requested', completed_at = NULL,
        execution_token = NULL, lease_expires_at = NULL
     WHERE id = p_task_id;
    UPDATE conversation_turn_checkpoints SET status = 'ready', updated_at = NOW()
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

CREATE OR REPLACE FUNCTION public.mark_stale_tool_invocation_uncertain(
    p_task_id UUID,
    p_turn_id UUID,
    p_tool_call_id TEXT,
    p_execution_token UUID,
    p_stale_after_seconds INTEGER DEFAULT 900
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_invocation tool_invocations%ROWTYPE;
    v_updated_count BIGINT;
    v_threshold INTEGER := GREATEST(COALESCE(p_stale_after_seconds, 900), 1);
BEGIN
    IF p_task_id IS NULL OR p_turn_id IS NULL OR p_execution_token IS NULL
       OR NULLIF(BTRIM(p_tool_call_id), '') IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_STALE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    SELECT * INTO v_invocation FROM tool_invocations
     WHERE task_id = p_task_id AND turn_id = p_turn_id
       AND tool_call_id = BTRIM(p_tool_call_id) FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;
    IF v_invocation.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', v_invocation.status);
    END IF;
    IF v_invocation.updated_at >= NOW() - make_interval(secs => v_threshold) THEN
        RETURN jsonb_build_object('outcome', 'fresh');
    END IF;
    UPDATE tool_invocations SET status = 'uncertain',
        error_message = LEFT('外部工具调用超过恢复阈值，结果未知；禁止自动重试。', 2000),
        completed_at = NOW(), updated_at = NOW()
     WHERE id = v_invocation.id AND status = 'running';
    GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    IF v_updated_count = 0 THEN
        RETURN jsonb_build_object('outcome', 'already_completed');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'uncertain',
        'error_message', '外部工具调用超过恢复阈值，结果未知；禁止自动重试。'
    );
END;
$$;

COMMENT ON TABLE public.conversation_turn_checkpoints IS
    'Conversation Actor 生产兼容 checkpoint；每个 task 保留最近可重放安全边界';
