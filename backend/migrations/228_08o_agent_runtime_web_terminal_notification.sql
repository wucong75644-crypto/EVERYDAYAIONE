-- 228.08o: expose the committed Web Runtime terminal payload to Projection.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_agent_runtime_web_terminal_notification_v1(
    p_task_id UUID, p_message_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_projection_worker'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'projection'
       OR p_task_id IS NULL OR p_message_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_WEB_TERMINAL_NOTIFICATION_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id;
    SELECT * INTO v_message FROM messages WHERE id = p_message_id;
    IF v_task.id IS NULL OR v_message.id IS NULL
       OR v_task.assistant_message_id IS DISTINCT FROM v_message.id
       OR v_task.status NOT IN ('completed', 'failed', 'cancelled')
       OR v_message.conversation_id IS DISTINCT FROM v_task.conversation_id
       OR v_message.org_id IS DISTINCT FROM v_task.org_id THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found',
        'task', jsonb_build_object(
            'id', v_task.id,
            'client_task_id', v_task.client_task_id,
            'external_task_id', v_task.external_task_id,
            'user_id', v_task.user_id,
            'org_id', v_task.org_id,
            'conversation_id', v_task.conversation_id,
            'status', v_task.status,
            'error_message', v_task.error_message
        ),
        'message', jsonb_build_object(
            'id', v_message.id,
            'conversation_id', v_message.conversation_id,
            'content', v_message.content,
            'role', v_message.role,
            'created_at', v_message.created_at,
            'status', v_message.status,
            'credits_cost', v_message.credits_cost,
            'is_error', v_message.is_error,
            'generation_params', v_message.generation_params,
            'client_request_id', v_message.client_request_id,
            'turn_id', v_message.turn_id,
            'reply_to_message_id', v_message.reply_to_message_id,
            'context_revision', v_message.context_revision,
            'message_kind', v_message.message_kind
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION get_agent_runtime_web_terminal_notification_v1(UUID, UUID)
FROM PUBLIC, everydayai, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai_authorization_worker,
    everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION get_agent_runtime_web_terminal_notification_v1(UUID, UUID)
TO everydayai_projection_worker;

RESET ROLE;
