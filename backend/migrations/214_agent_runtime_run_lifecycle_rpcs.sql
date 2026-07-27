-- 214: Agent Runtime Run lifecycle RPCs.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION set_agent_run_waiting(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_waiting_status TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF p_waiting_status NOT IN (
        'waiting_actions', 'waiting_interaction', 'paused'
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_WAIT_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_runs SET status = p_waiting_status,
           execution_token = NULL, lease_expires_at = NULL,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts
       SET ended_at = clock_timestamp(), outcome = 'completed'
     WHERE execution_token = p_execution_token AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.waiting', v_run.id, NULL, p_execution_token,
        'system', session_user, jsonb_build_object('status', p_waiting_status),
        ARRAY['web_runtime']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'transitioned', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION wake_agent_run(
    p_run_id UUID, p_expected_state_version BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_run.status NOT IN (
        'waiting_actions', 'waiting_interaction', 'paused'
    ) OR (v_run.status = 'waiting_actions' AND v_run.blocking_action_count <> 0)
       OR (v_run.status = 'waiting_interaction'
           AND v_run.open_interaction_count <> 0) THEN
        RETURN jsonb_build_object('outcome', 'not_ready');
    END IF;
    UPDATE agent_runs SET status = 'queued',
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.resumed', v_run.id, NULL, gen_random_uuid(),
        'system', session_user, '{}'::JSONB, ARRAY['web_runtime']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'transitioned', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION _finish_agent_run(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_status TEXT, p_result_hash TEXT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status IN ('completed', 'failed', 'cancelled') THEN
        IF v_run.status = p_status
           AND v_run.result_hash IS NOT DISTINCT FROM p_result_hash
           AND v_run.terminal_reason IS NOT DISTINCT FROM p_reason THEN
            RETURN jsonb_build_object(
                'outcome', 'already_' || p_status, 'entity_id', v_run.id,
                'state_version', v_run.state_version
            );
        END IF;
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF p_status = 'completed' AND (
        v_run.blocking_action_count <> 0 OR v_run.open_interaction_count <> 0
        OR NOT EXISTS (
            SELECT 1
              FROM agent_model_steps step
             WHERE step.run_id = p_run_id
               AND step.step_number = (
                   SELECT MAX(latest.step_number)
                     FROM agent_model_steps latest
                    WHERE latest.run_id = p_run_id
               )
               AND step.status = 'completed'
               AND step.stop_reason IN ('final', 'structured_final')
        )
    ) THEN RETURN jsonb_build_object('outcome', 'not_ready'); END IF;
    UPDATE agent_runs SET status = p_status, execution_token = NULL,
           lease_expires_at = NULL, completed_at = clock_timestamp(),
           terminal_reason = p_reason, result_hash = p_result_hash,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET ended_at = clock_timestamp(),
           outcome = CASE WHEN p_status = 'completed' THEN 'completed'
                          WHEN p_status = 'cancelled' THEN 'cancelled'
                          ELSE 'failed' END
     WHERE execution_token = p_execution_token AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.' || p_status, v_run.id, NULL,
        p_execution_token, 'system', session_user,
        jsonb_build_object('reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', p_status, 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION complete_agent_run(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_result_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    RETURN _finish_agent_run(
        p_run_id, p_execution_token, p_expected_state_version,
        'completed', p_result_hash, 'completed'
    );
END;
$$;

CREATE FUNCTION fail_agent_run(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    RETURN _finish_agent_run(
        p_run_id, p_execution_token, p_expected_state_version,
        'failed', NULL, p_error_code
    );
END;
$$;

CREATE FUNCTION cancel_agent_run(
    p_run_id UUID, p_expected_state_version BIGINT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM _assert_agent_runtime_actor(TRUE);
    ELSE
        PERFORM _assert_agent_runtime_actor(FALSE);
    END IF;
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status = 'cancelled' THEN
        IF v_run.terminal_reason IS DISTINCT FROM p_reason THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_cancelled', 'entity_id', v_run.id,
            'state_version', v_run.state_version
        );
    END IF;
    IF v_run.status IN ('completed', 'failed') THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF session_user <> 'everydayai_worker' AND (
        tenant_org_id() IS DISTINCT FROM v_run.org_id
        OR NOT EXISTS (
            SELECT 1 FROM agent_runtime_sessions session
             WHERE session.id = v_run.session_id
               AND (
                   (session.scope_kind = 'user'
                    AND session.user_id = tenant_actor_user_id())
                   OR (
                       session.scope_kind = 'channel'
                       AND EXISTS (
                           SELECT 1 FROM org_members member
                            WHERE member.org_id = session.org_id
                              AND member.user_id = tenant_actor_user_id()
                              AND member.status = 'active'
                       )
                   )
               )
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    UPDATE agent_runs SET status = 'cancelled', execution_token = NULL,
           lease_expires_at = NULL, completed_at = clock_timestamp(),
           terminal_reason = p_reason, state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET ended_at = clock_timestamp(),
           outcome = 'cancelled'
     WHERE run_id = p_run_id AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.cancelled', v_run.id, NULL,
        gen_random_uuid(), CASE WHEN session_user = 'everydayai_worker'
            THEN 'system' ELSE 'user' END, session_user,
        jsonb_build_object('reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

REVOKE ALL ON FUNCTION
    _finish_agent_run(UUID, UUID, BIGINT, TEXT, TEXT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

REVOKE ALL ON FUNCTION
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT),
    wake_agent_run(UUID, BIGINT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT),
    cancel_agent_run(UUID, BIGINT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION cancel_agent_run(UUID, BIGINT, TEXT)
TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT),
    wake_agent_run(UUID, BIGINT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT)
TO everydayai_worker;

RESET ROLE;
