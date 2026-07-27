SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION prepare_model_attempt(
    p_step_id UUID, p_run_execution_token UUID,
    p_expected_step_version BIGINT, p_worker_id TEXT, p_request_hash TEXT,
    p_idempotency_key TEXT, p_provider TEXT, p_request_receipt JSONB,
    p_reserved_credits INTEGER, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_attempt agent_model_attempts%ROWTYPE;
    v_previous agent_model_attempts%ROWTYPE;
    v_reservation JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_step FROM agent_model_steps WHERE id = p_step_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_step.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_step.run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps WHERE id = p_step_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_step.status <> 'running'
       OR v_step.state_version <> p_expected_step_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF p_request_hash !~ '^[0-9a-f]{64}$'
       OR NULLIF(BTRIM(p_worker_id), '') IS NULL
       OR NULLIF(BTRIM(p_idempotency_key), '') IS NULL
       OR NULLIF(BTRIM(p_provider), '') IS NULL
       OR jsonb_typeof(p_request_receipt) IS DISTINCT FROM 'object'
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'AGENT_MODEL_ATTEMPT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_attempt FROM agent_model_attempts
     WHERE model_step_id = p_step_id AND idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF FOUND THEN
        IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
            RETURN jsonb_build_object('outcome', 'idempotency_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_prepared', 'attempt_id', v_attempt.id,
            'model_step_id', v_attempt.model_step_id,
            'attempt_number', v_attempt.attempt_number,
            'status', v_attempt.status, 'dispatch_phase', v_attempt.dispatch_phase,
            'retry_disposition', v_attempt.retry_disposition,
            'state_version', v_attempt.state_version
        );
    END IF;
    SELECT * INTO v_previous FROM agent_model_attempts
     WHERE model_step_id = p_step_id ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE;
    IF FOUND AND v_previous.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF FOUND AND NOT (
        v_previous.status = 'failed'
        AND v_previous.retry_disposition = 'retry_safe'
    ) THEN RETURN jsonb_build_object('outcome', 'unresolved_attempt'); END IF;
    INSERT INTO agent_model_attempts(
        model_step_id, run_id, session_id, org_id, user_id, attempt_number,
        request_hash, idempotency_key, provider, request_receipt,
        worker_id, execution_token, lease_expires_at
    ) VALUES (
        v_step.id, v_step.run_id, v_step.session_id, v_step.org_id, v_step.user_id,
        COALESCE(v_previous.attempt_number, 0) + 1, p_request_hash,
        BTRIM(p_idempotency_key), BTRIM(p_provider), p_request_receipt,
        BTRIM(p_worker_id), p_run_execution_token,
        clock_timestamp() + make_interval(secs => p_lease_seconds)
    ) RETURNING * INTO v_attempt;
    IF NOT EXISTS (
        SELECT 1 FROM agent_model_credit_settlements
         WHERE model_step_id = p_step_id
    ) THEN
        v_reservation := _reserve_agent_model_credits(
            v_step, v_attempt.id, p_reserved_credits
        );
        IF v_reservation->>'outcome' = 'insufficient_credits' THEN
            DELETE FROM agent_model_attempts WHERE id = v_attempt.id;
            RETURN jsonb_build_object('outcome', 'insufficient_credits');
        END IF;
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'prepared', 'attempt_id', v_attempt.id,
        'model_step_id', v_attempt.model_step_id,
        'attempt_number', v_attempt.attempt_number, 'status', v_attempt.status,
        'dispatch_phase', v_attempt.dispatch_phase,
        'retry_disposition', v_attempt.retry_disposition,
        'state_version', v_attempt.state_version
    );
END;
$$;

CREATE FUNCTION start_model_attempt_dispatch(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_model_attempts%ROWTYPE; v_run agent_runs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.status = 'dispatching' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_dispatching', 'attempt_id', v_attempt.id,
            'state_version', v_attempt.state_version
        );
    END IF;
    IF v_attempt.status <> 'prepared'
       OR v_attempt.state_version <> p_expected_attempt_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_model_attempts SET status = 'dispatching',
           dispatch_phase = 'request_started', state_version = state_version + 1,
           dispatched_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'dispatching', 'attempt_id', v_attempt.id,
        'status', v_attempt.status, 'dispatch_phase', v_attempt.dispatch_phase,
        'state_version', v_attempt.state_version
    );
END;
$$;

CREATE FUNCTION mark_model_attempt_response_started(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT,
    p_provider_request_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_model_attempts%ROWTYPE; v_run agent_runs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.dispatch_phase = 'response_started' THEN
        IF v_attempt.provider_request_id IS DISTINCT FROM p_provider_request_id THEN
            RETURN jsonb_build_object('outcome', 'receipt_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_started', 'attempt_id', v_attempt.id,
            'state_version', v_attempt.state_version
        );
    END IF;
    IF v_attempt.status <> 'dispatching'
       OR v_attempt.state_version <> p_expected_attempt_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_model_attempts SET dispatch_phase = 'response_started',
           provider_request_id = p_provider_request_id,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'response_started', 'attempt_id', v_attempt.id,
        'dispatch_phase', v_attempt.dispatch_phase,
        'state_version', v_attempt.state_version
    );
END;
$$;

CREATE FUNCTION record_model_attempt_unknown(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT,
    p_dispatch_phase TEXT, p_retry_disposition TEXT, p_ambiguity_evidence JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_model_attempts%ROWTYPE; v_run agent_runs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.status = 'unknown' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_unknown', 'attempt_id', v_attempt.id,
            'state_version', v_attempt.state_version
        );
    END IF;
    IF v_attempt.status <> 'dispatching'
       OR v_attempt.state_version <> p_expected_attempt_version
       OR p_dispatch_phase NOT IN ('request_started', 'response_started')
       OR p_retry_disposition NOT IN ('forbidden', 'reconcile_only')
       OR jsonb_typeof(p_ambiguity_evidence) IS DISTINCT FROM 'object' THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_model_attempts SET status = 'unknown',
           dispatch_phase = p_dispatch_phase,
           retry_disposition = p_retry_disposition,
           ambiguity_evidence = p_ambiguity_evidence,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'unknown', 'attempt_id', v_attempt.id,
        'status', v_attempt.status, 'state_version', v_attempt.state_version
    );
END;
$$;

CREATE FUNCTION complete_model_attempt_without_actions(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_expected_step_version BIGINT,
    p_request_hash TEXT, p_response_receipt JSONB, p_response_hash TEXT,
    p_stop_reason TEXT, p_provider_stop_reason TEXT, p_usage JSONB,
    p_actual_credits INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE; v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE; v_event JSONB; v_settlement JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF p_stop_reason = 'tool_calls' THEN
        RETURN jsonb_build_object('outcome', 'handoff_tool_calls');
    END IF;
    IF p_stop_reason NOT IN ('final', 'structured_final') THEN
        RAISE EXCEPTION 'AGENT_MODEL_TERMINAL_STOP_INVALID' USING ERRCODE = '22023';
    END IF;
    IF v_run.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'run_cancelled_use_late_receipt');
    END IF;
    IF v_attempt.status = 'completed' THEN
        IF v_attempt.response_hash IS DISTINCT FROM p_response_hash THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object('outcome', 'already_completed');
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.status NOT IN ('dispatching', 'unknown')
       OR v_attempt.state_version <> p_expected_attempt_version
       OR v_step.status <> 'running'
       OR v_step.state_version <> p_expected_step_version
       OR COALESCE(p_response_hash !~ '^[0-9a-f]{64}$', TRUE)
       OR jsonb_typeof(p_response_receipt) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_usage) IS DISTINCT FROM 'object' THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    v_settlement := _settle_agent_model_credits(
        v_step, v_attempt.id, p_response_hash, p_actual_credits
    );
    IF v_settlement->>'outcome' = 'terminal_conflict' THEN
        RETURN v_settlement;
    END IF;
    UPDATE agent_model_attempts SET status = 'completed',
           response_receipt = p_response_receipt, response_hash = p_response_hash,
           usage = p_usage, retry_disposition = 'forbidden',
           state_version = state_version + 1, completed_at = clock_timestamp(),
           updated_at = clock_timestamp() WHERE id = v_attempt.id
    RETURNING * INTO v_attempt;
    UPDATE agent_model_steps SET status = 'completed',
           response_receipt = p_response_receipt, stop_reason = p_stop_reason,
           provider_stop_reason = p_provider_stop_reason,
           input_tokens = COALESCE((p_usage->>'input_tokens')::BIGINT, 0),
           output_tokens = COALESCE((p_usage->>'output_tokens')::BIGINT, 0),
           reasoning_tokens = COALESCE((p_usage->>'reasoning_tokens')::BIGINT, 0),
           state_version = state_version + 1, completed_at = clock_timestamp(),
           updated_at = clock_timestamp() WHERE id = v_step.id
    RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_step.session_id, 'model_step.completed', v_step.run_id, v_step.id,
        p_run_execution_token, 'model', session_user,
        jsonb_build_object('stop_reason', p_stop_reason, 'attempt_id', v_attempt.id),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'completed', 'attempt_id', v_attempt.id,
        'model_step_id', v_step.id, 'state_version', v_attempt.state_version,
        'event_sequence', v_event->'event_sequence',
        'settlement_outcome', v_settlement->'outcome'
    );
END;
$$;

CREATE FUNCTION fail_model_attempt_and_step(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_expected_step_version BIGINT,
    p_request_hash TEXT, p_error_code TEXT,
    p_retry_disposition TEXT DEFAULT 'forbidden'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE; v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE; v_event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'run_cancelled_use_late_receipt');
    END IF;
    IF v_attempt.status = 'failed' THEN
        RETURN jsonb_build_object('outcome', 'already_failed');
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.status NOT IN ('prepared', 'dispatching', 'unknown')
       OR v_attempt.state_version <> p_expected_attempt_version
       OR v_step.status <> 'running'
       OR v_step.state_version <> p_expected_step_version
       OR p_retry_disposition NOT IN ('forbidden', 'retry_safe')
       OR NULLIF(BTRIM(p_error_code), '') IS NULL THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF p_retry_disposition = 'retry_safe'
       AND NOT (v_attempt.status = 'prepared'
                AND v_attempt.dispatch_phase = 'prepared') THEN
        RAISE EXCEPTION 'AGENT_MODEL_RETRY_EVIDENCE_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_model_attempts SET status = 'failed',
           retry_disposition = p_retry_disposition,
           state_version = state_version + 1, completed_at = clock_timestamp(),
           updated_at = clock_timestamp() WHERE id = v_attempt.id
    RETURNING * INTO v_attempt;
    IF p_retry_disposition = 'retry_safe' THEN
        RETURN jsonb_build_object('outcome', 'failed', 'attempt_id', v_attempt.id,
            'model_step_id', v_step.id, 'state_version', v_attempt.state_version);
    END IF;
    PERFORM _release_agent_model_credits(v_step.id);
    UPDATE agent_model_steps SET status = 'failed', stop_reason = 'provider_error',
           terminal_reason = LEFT(p_error_code, 200),
           state_version = state_version + 1, completed_at = clock_timestamp(),
           updated_at = clock_timestamp() WHERE id = v_step.id
    RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_step.session_id, 'model_step.failed', v_step.run_id, v_step.id,
        p_run_execution_token, 'model', session_user,
        jsonb_build_object('error_code', p_error_code, 'attempt_id', v_attempt.id),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'failed', 'attempt_id', v_attempt.id,
        'model_step_id', v_step.id, 'state_version', v_attempt.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE OR REPLACE FUNCTION cancel_agent_run(
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
            SELECT 1 FROM agent_runtime_sessions session WHERE session.id = v_run.session_id
             AND ((session.scope_kind = 'user'
                   AND session.user_id = tenant_actor_user_id())
                  OR (session.scope_kind = 'channel' AND EXISTS (
                    SELECT 1 FROM org_members member
                     WHERE member.org_id = session.org_id
                       AND member.user_id = tenant_actor_user_id()
                       AND member.status = 'active'))))
    ) THEN RAISE EXCEPTION 'AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH'
        USING ERRCODE = '42501'; END IF;
    PERFORM _cancel_agent_model_work(p_run_id);
    UPDATE agent_runs SET status = 'cancelled', execution_token = NULL,
           lease_expires_at = NULL, completed_at = clock_timestamp(),
           terminal_reason = p_reason, state_version = state_version + 1,
           updated_at = clock_timestamp() WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET ended_at = clock_timestamp(), outcome = 'cancelled'
     WHERE run_id = p_run_id AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.cancelled', v_run.id, NULL, gen_random_uuid(),
        CASE WHEN session_user = 'everydayai_worker' THEN 'system' ELSE 'user' END,
        session_user, jsonb_build_object('reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence');
END;
$$;

REVOKE ALL ON FUNCTION
    prepare_model_attempt(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, TEXT, JSONB, INTEGER, INTEGER),
    start_model_attempt_dispatch(UUID, UUID, BIGINT, TEXT),
    mark_model_attempt_response_started(UUID, UUID, BIGINT, TEXT, TEXT),
    record_model_attempt_unknown(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, JSONB),
    complete_model_attempt_without_actions(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, JSONB, INTEGER
    ),
    fail_model_attempt_and_step(UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    prepare_model_attempt(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, TEXT, JSONB, INTEGER, INTEGER),
    start_model_attempt_dispatch(UUID, UUID, BIGINT, TEXT),
    mark_model_attempt_response_started(UUID, UUID, BIGINT, TEXT, TEXT),
    record_model_attempt_unknown(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, JSONB),
    complete_model_attempt_without_actions(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, JSONB, INTEGER
    ),
    fail_model_attempt_and_step(UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT)
TO everydayai_worker;

RESET ROLE;
