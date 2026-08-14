-- AR-18-A1.2-B1.1: make task cancel callable without client-side hash drift.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION request_agent_runtime_task_cancel_v2(
    p_task_id UUID, p_message_id UUID, p_org_id UUID, p_user_id UUID,
    p_session_id UUID, p_submit_command_id UUID, p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_scope_user_id UUID;
    v_request_hash TEXT;
BEGIN
    SELECT session.user_id INTO v_scope_user_id
      FROM agent_runtime_sessions session
     WHERE session.id = p_session_id;
    v_request_hash := _agent_runtime_task_cancel_request_hash(
        p_task_id, p_message_id, p_org_id, v_scope_user_id, p_user_id,
        p_session_id, p_submit_command_id, p_idempotency_key);
    RETURN request_agent_runtime_task_cancel_v1(
        p_task_id, p_message_id, p_org_id, p_user_id,
        p_session_id, p_submit_command_id, p_idempotency_key, v_request_hash);
END;
$$;

REVOKE ALL ON FUNCTION
    request_agent_runtime_task_cancel_v1(
        UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT),
    request_agent_runtime_task_cancel_v2(
        UUID,UUID,UUID,UUID,UUID,UUID,TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai,
    everydayai_agent_runtime_worker, everydayai_agent_model_gateway,
    everydayai_projection_worker, everydayai_authorization_worker,
    everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION request_agent_runtime_task_cancel_v2(
    UUID,UUID,UUID,UUID,UUID,UUID,TEXT)
TO everydayai_runtime, everydayai_wecom_runtime;

RESET ROLE;
