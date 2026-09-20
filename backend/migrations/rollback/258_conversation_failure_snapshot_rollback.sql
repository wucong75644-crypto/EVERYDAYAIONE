-- Restore the previous failure function; existing message snapshots are retained.
CREATE OR REPLACE FUNCTION fail_generation_turn(
    p_task_id UUID,
    p_execution_token UUID,
    p_error_code TEXT,
    p_error_message TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_conversation_id UUID;
BEGIN
    IF p_execution_token IS NULL
       OR NULLIF(BTRIM(p_error_code), '') IS NULL
       OR NULLIF(BTRIM(p_error_message), '') IS NULL THEN
        RAISE EXCEPTION 'ACTOR_FAIL_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT conversation_id INTO v_conversation_id FROM tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;

    IF v_task.id IS NULL OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_task.org_id IS DISTINCT FROM v_conversation.org_id THEN
        RAISE EXCEPTION 'ACTOR_FAIL_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'failed'
       AND v_task.execution_token IS NOT DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'already_failed', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    UPDATE tasks
       SET status = 'failed',
           fail_code = LEFT(p_error_code, 50),
           error_message = p_error_message,
           completed_at = NOW(),
           lease_expires_at = NULL,
           terminal_reason = 'execution_failed'
     WHERE id = p_task_id;
    UPDATE messages
       SET status = 'failed',
           is_error = TRUE
     WHERE id = v_task.assistant_message_id;
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object('outcome', 'failed', 'task_id', p_task_id);
END;
$$;

