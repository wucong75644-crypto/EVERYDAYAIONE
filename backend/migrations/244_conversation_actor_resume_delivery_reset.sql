-- 244: RESUME creates a new page-delivery attempt.
--
-- The paused message remains the durable historical snapshot.  DeliveryProgress
-- belongs only to the currently visible attempt, while ReplayCheckpoint remains
-- available to restore the model context.

BEGIN;

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

    -- Old page events/snapshots must never be replayed into the new attempt.
    -- Deleting the session cascades its events; the paused message itself and
    -- ReplayCheckpoint are intentionally retained.
    DELETE FROM conversation_delivery_sessions WHERE task_id = p_task_id;

    UPDATE tasks SET status = 'pending', error_message = NULL,
        terminal_reason = 'resume_requested', completed_at = NULL,
        execution_token = NULL, lease_expires_at = NULL,
        accumulated_content = '', accumulated_blocks = '[]'::JSONB
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
        'client_task_id', v_task.client_task_id,
        'delivery_reset', TRUE
    );
END;
$$;

COMMIT;
