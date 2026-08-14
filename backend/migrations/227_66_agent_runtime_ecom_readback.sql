-- 227.66: tenant-scoped, read-only e-commerce Runtime result readback.
-- Additive only; pending/unknown states are never converted to completion.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION read_agent_runtime_ecom_model_v1(
    p_conversation_id UUID, p_org_id UUID, p_user_id UUID,
    p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    s agent_runtime_sessions%ROWTYPE;
    c agent_session_commands%ROWTYPE;
    r agent_runs%ROWTYPE;
    step agent_model_steps%ROWTYPE;
    result agent_model_results%ROWTYPE;
    attempt_status TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    SELECT * INTO s FROM agent_runtime_sessions
     WHERE conversation_id=p_conversation_id;
    IF NOT FOUND OR s.org_id IS DISTINCT FROM p_org_id
       OR s.user_id IS DISTINCT FROM p_user_id
       OR tenant_org_id() IS DISTINCT FROM p_org_id
       OR tenant_actor_user_id() IS DISTINCT FROM p_user_id THEN
        RETURN jsonb_build_object('outcome','not_found');
    END IF;
    SELECT * INTO c FROM agent_session_commands
     WHERE session_id=s.id AND idempotency_key=BTRIM(p_idempotency_key);
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    SELECT * INTO r FROM agent_runs WHERE command_id=c.id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','accepted'); END IF;
    SELECT status INTO attempt_status FROM agent_model_attempts
     WHERE model_step_id=(SELECT id FROM agent_model_steps WHERE run_id=r.id
       ORDER BY step_number DESC LIMIT 1)
     ORDER BY created_at DESC LIMIT 1;
    IF attempt_status IN ('unknown','dispatching') THEN
        RETURN jsonb_build_object('outcome','unknown','run_id',r.id,
            'reason_code','model_attempt_requires_reconcile');
    END IF;
    IF r.status IN ('queued','running','waiting_actions','waiting_interaction','paused') THEN
        RETURN jsonb_build_object('outcome','pending','run_id',r.id);
    END IF;
    IF r.status IN ('failed','cancelled') THEN
        RETURN jsonb_build_object('outcome',r.status,'run_id',r.id,
            'reason_code',r.terminal_reason);
    END IF;
    SELECT * INTO step FROM agent_model_steps WHERE run_id=r.id
     ORDER BY step_number DESC LIMIT 1;
    SELECT * INTO result FROM agent_model_results WHERE model_step_id=step.id;
    IF step.id IS NULL OR result.id IS NULL OR step.status <> 'completed'
       OR step.stop_reason NOT IN ('final','structured_final') THEN
        RETURN jsonb_build_object('outcome','reconcile','run_id',r.id,
            'reason_code','runtime_result_not_projectable');
    END IF;
    RETURN jsonb_build_object('outcome','completed','run_id',r.id,
        'model_step_id',step.id,'content',result.text_content,
        'structured_content',result.structured_content);
END;
$$;

REVOKE ALL ON FUNCTION read_agent_runtime_ecom_model_v1(UUID,UUID,UUID,TEXT)
 FROM PUBLIC, everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION read_agent_runtime_ecom_model_v1(UUID,UUID,UUID,TEXT)
 TO everydayai_runtime, everydayai_wecom_runtime;
RESET ROLE;
