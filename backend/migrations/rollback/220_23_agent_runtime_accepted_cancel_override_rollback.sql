SET LOCAL ROLE everydayai_owner;

-- Restore migration 218_04 cancellation semantics exactly.
CREATE OR REPLACE FUNCTION cancel_agent_run(
    p_run_id UUID, p_expected_state_version BIGINT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_run agent_runs%ROWTYPE; v_action agent_actions%ROWTYPE;
    v_event JSONB; v_session_id UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM _assert_agent_runtime_actor(TRUE);
    ELSE
        PERFORM _assert_agent_runtime_actor(FALSE);
    END IF;
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status = 'cancelled' THEN
        IF v_run.terminal_reason IS DISTINCT FROM p_reason THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_cancelled', 'entity_id', v_run.id,
            'state_version', v_run.state_version);
    END IF;
    IF v_run.status IN ('completed', 'failed') THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF session_user <> 'everydayai_worker' AND (
        tenant_org_id() IS DISTINCT FROM v_run.org_id OR NOT EXISTS (
            SELECT 1 FROM agent_runtime_sessions session
             WHERE session.id = v_run.session_id
               AND ((session.scope_kind = 'user'
                     AND session.user_id = tenant_actor_user_id())
                 OR (session.scope_kind = 'channel' AND EXISTS (
                    SELECT 1 FROM org_members member
                     WHERE member.org_id = session.org_id
                       AND member.user_id = tenant_actor_user_id()
                       AND member.status = 'active'))))
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    PERFORM _cancel_agent_run_action_work(p_run_id);
    UPDATE agent_action_attempts SET status = 'cancelled',
           reconciliation_token = NULL, reconciliation_lease_expires_at = NULL,
           state_version = state_version + 1, ended_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE action_id IN (SELECT id FROM agent_actions WHERE run_id = p_run_id)
       AND status NOT IN ('completed', 'failed', 'cancelled');
    FOR v_action IN
        UPDATE agent_actions SET status = 'cancelled',
               terminal_reason = LEFT(p_reason, 200),
               state_version = state_version + 1,
               completed_at = clock_timestamp(), updated_at = clock_timestamp()
         WHERE run_id = p_run_id
           AND status NOT IN ('completed', 'failed', 'rejected', 'cancelled')
        RETURNING *
    LOOP
        PERFORM append_agent_runtime_event(
            v_action.session_id, 'action.cancelled', v_action.run_id,
            v_action.model_step_id, v_action.id, 'system', session_user,
            jsonb_build_object('action_id', v_action.id, 'reason', p_reason),
            ARRAY['web_runtime', 'audit']::TEXT[]);
    END LOOP;
    UPDATE agent_runs SET status = 'cancelled', blocking_action_count = 0,
           execution_token = NULL, lease_expires_at = NULL,
           completed_at = clock_timestamp(), terminal_reason = p_reason,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET ended_at = clock_timestamp(),
           outcome = 'cancelled'
     WHERE run_id = p_run_id AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.cancelled', v_run.id, NULL, gen_random_uuid(),
        CASE WHEN session_user = 'everydayai_worker'
             THEN 'system' ELSE 'user' END,
        session_user, jsonb_build_object('reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence');
END;
$$;

RESET ROLE;
