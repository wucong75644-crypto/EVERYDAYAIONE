-- 218_04: Accepted/unknown reconciliation and Run-wide cancellation.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION mark_agent_action_accepted(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_state_version BIGINT, p_request_hash TEXT,
    p_external_receipt JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE; v_action agent_actions%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    PERFORM 1 FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    SELECT * INTO v_action FROM agent_actions
     WHERE id = v_attempt.action_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    IF v_attempt.status = 'accepted' THEN
        IF v_attempt.external_receipt IS DISTINCT FROM p_external_receipt THEN
            RETURN jsonb_build_object('outcome', 'receipt_conflict');
        END IF;
        RETURN jsonb_build_object('outcome', 'already_accepted',
                                  'state_version', v_attempt.state_version);
    END IF;
    IF v_attempt.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.lease_expires_at <= clock_timestamp()
       OR v_attempt.state_version <> p_expected_state_version
       OR v_attempt.request_hash IS DISTINCT FROM p_request_hash
       OR v_attempt.status <> 'dispatching'
       OR v_action.status <> 'running'
       OR jsonb_typeof(p_external_receipt) IS DISTINCT FROM 'object'
       OR NOT _agent_action_json_is_safe(p_external_receipt)
       OR p_external_receipt = '{}'::JSONB THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_action_attempts SET status = 'accepted',
           dispatch_phase = 'accepted', external_receipt = p_external_receipt,
           accepted_at = clock_timestamp(), state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    UPDATE agent_actions SET status = 'accepted',
           accepted_at = clock_timestamp(), state_version = state_version + 1,
           updated_at = clock_timestamp() WHERE id = v_action.id;
    PERFORM append_agent_runtime_event(
        v_action.session_id, 'action.accepted', v_action.run_id,
        v_action.model_step_id, v_action.id, 'executor', session_user,
        jsonb_build_object('action_id', v_action.id),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    RETURN jsonb_build_object('outcome', 'accepted',
                              'state_version', v_attempt.state_version);
END;
$$;

CREATE FUNCTION record_agent_action_unknown(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_state_version BIGINT, p_request_hash TEXT,
    p_ambiguity_evidence JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE; v_action agent_actions%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    PERFORM 1 FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    SELECT * INTO v_action FROM agent_actions
     WHERE id = v_attempt.action_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    IF v_attempt.status = 'unknown' THEN
        IF v_attempt.ambiguity_evidence IS DISTINCT FROM p_ambiguity_evidence THEN
            RETURN jsonb_build_object('outcome', 'receipt_conflict');
        END IF;
        RETURN jsonb_build_object('outcome', 'already_unknown',
                                  'state_version', v_attempt.state_version);
    END IF;
    IF v_attempt.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.lease_expires_at <= clock_timestamp()
       OR v_attempt.state_version <> p_expected_state_version
       OR v_attempt.request_hash IS DISTINCT FROM p_request_hash
       OR v_attempt.status NOT IN ('dispatching', 'accepted')
       OR v_action.status NOT IN ('running', 'accepted')
       OR jsonb_typeof(p_ambiguity_evidence) IS DISTINCT FROM 'object'
       OR NOT _agent_action_json_is_safe(p_ambiguity_evidence)
       OR p_ambiguity_evidence = '{}'::JSONB THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_action_attempts SET status = 'unknown',
           ambiguity_evidence = p_ambiguity_evidence,
           retry_disposition = 'retry_after_reconcile',
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    UPDATE agent_actions SET status = 'unknown',
           retry_disposition = 'retry_after_reconcile',
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = v_action.id;
    PERFORM append_agent_runtime_event(
        v_action.session_id, 'action.unknown', v_action.run_id,
        v_action.model_step_id, v_action.id, 'executor', session_user,
        jsonb_build_object('action_id', v_action.id),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    RETURN jsonb_build_object('outcome', 'unknown',
                              'state_version', v_attempt.state_version);
END;
$$;

CREATE FUNCTION claim_agent_action_reconciliation(
    p_attempt_id UUID, p_expected_state_version BIGINT,
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE; v_token UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    PERFORM 1 FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_actions WHERE id = v_attempt.action_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    IF v_attempt.status NOT IN ('accepted', 'unknown') THEN
        RETURN jsonb_build_object('outcome', 'not_reconcilable');
    END IF;
    IF v_attempt.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_attempt.reconciliation_token IS NOT NULL
       AND v_attempt.reconciliation_lease_expires_at > clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'busy');
    END IF;
    IF NULLIF(btrim(p_worker_id), '') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'AGENT_ACTION_RECONCILE_INVALID' USING ERRCODE = '22023';
    END IF;
    v_token := gen_random_uuid();
    UPDATE agent_action_attempts SET reconciliation_token = v_token,
           reconciliation_lease_expires_at =
               clock_timestamp() + make_interval(secs => p_lease_seconds),
           worker_id = btrim(p_worker_id), state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'claimed', 'attempt_id', v_attempt.id,
        'execution_token', v_token,
        'lease_expires_at', v_attempt.reconciliation_lease_expires_at,
        'state_version', v_attempt.state_version);
END;
$$;

CREATE FUNCTION renew_agent_action_reconciliation(
    p_attempt_id UUID, p_reconciliation_token UUID,
    p_expected_state_version BIGINT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF v_attempt.reconciliation_token IS DISTINCT FROM p_reconciliation_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.status NOT IN ('accepted', 'unknown')
       OR v_attempt.reconciliation_lease_expires_at <= clock_timestamp()
       OR v_attempt.state_version <> p_expected_state_version
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_action_attempts SET reconciliation_lease_expires_at =
               clock_timestamp() + make_interval(secs => p_lease_seconds),
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'renewed', 'state_version', v_attempt.state_version,
        'lease_expires_at', v_attempt.reconciliation_lease_expires_at);
END;
$$;

CREATE FUNCTION resolve_agent_action_reconciliation(
    p_attempt_id UUID, p_reconciliation_token UUID,
    p_expected_state_version BIGINT, p_request_hash TEXT,
    p_resolution TEXT, p_result JSONB DEFAULT NULL,
    p_ambiguity_evidence JSONB DEFAULT '{}'::JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF p_resolution = 'still_unknown' THEN
        PERFORM 1 FROM agent_runtime_sessions
         WHERE id = v_attempt.session_id FOR UPDATE;
        PERFORM 1 FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
        PERFORM 1 FROM agent_actions
         WHERE id = v_attempt.action_id FOR UPDATE;
        SELECT * INTO v_attempt FROM agent_action_attempts
         WHERE id = p_attempt_id FOR UPDATE;
        IF v_attempt.reconciliation_token IS DISTINCT FROM p_reconciliation_token
           OR v_attempt.reconciliation_lease_expires_at <= clock_timestamp()
           OR v_attempt.state_version <> p_expected_state_version
           OR v_attempt.status NOT IN ('accepted', 'unknown')
           OR jsonb_typeof(p_ambiguity_evidence) IS DISTINCT FROM 'object'
           OR NOT _agent_action_json_is_safe(p_ambiguity_evidence)
           OR p_ambiguity_evidence = '{}'::JSONB THEN
            RETURN jsonb_build_object('outcome', 'stale_version');
        END IF;
        UPDATE agent_action_attempts SET status = 'unknown',
               ambiguity_evidence = p_ambiguity_evidence,
               reconciliation_token = NULL,
               reconciliation_lease_expires_at = NULL,
               state_version = state_version + 1, updated_at = clock_timestamp()
         WHERE id = p_attempt_id RETURNING * INTO v_attempt;
        UPDATE agent_actions SET status = 'unknown',
               state_version = state_version + 1,
               updated_at = clock_timestamp() WHERE id = v_attempt.action_id;
        UPDATE agent_runs SET state_version = state_version + 1,
               updated_at = clock_timestamp() WHERE id = v_attempt.run_id;
        RETURN jsonb_build_object('outcome', 'still_unknown',
                                  'state_version', v_attempt.state_version);
    END IF;
    IF p_resolution NOT IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'AGENT_ACTION_RESOLUTION_INVALID' USING ERRCODE = '22023';
    END IF;
    RETURN _finish_agent_action(
        p_attempt_id, p_reconciliation_token, p_expected_state_version,
        p_request_hash, p_resolution, p_result);
END;
$$;

CREATE FUNCTION _cancel_agent_run_action_work(p_run_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_step agent_model_steps%ROWTYPE;
    v_attempt agent_model_attempts%ROWTYPE;
BEGIN
    SELECT * INTO v_step FROM agent_model_steps
     WHERE run_id = p_run_id AND status IN ('pending', 'running')
     ORDER BY step_number DESC LIMIT 1 FOR UPDATE;
    IF FOUND THEN
        SELECT * INTO v_attempt FROM agent_model_attempts
         WHERE model_step_id = v_step.id
           AND status IN ('prepared', 'dispatching', 'unknown')
         ORDER BY id FOR UPDATE;
    END IF;
    PERFORM 1 FROM agent_actions WHERE run_id = p_run_id
     ORDER BY id FOR UPDATE;
    PERFORM 1 FROM agent_action_attempts attempt
     JOIN agent_actions action ON action.id = attempt.action_id
     WHERE action.run_id = p_run_id AND attempt.ended_at IS NULL
     ORDER BY attempt.id FOR UPDATE OF attempt;
    IF v_step.id IS NOT NULL THEN
        PERFORM _release_agent_model_credits(v_step.id);
        IF v_attempt.id IS NOT NULL THEN
            UPDATE agent_model_attempts SET status = 'cancelled',
                   retry_disposition = 'forbidden',
                   state_version = state_version + 1,
                   completed_at = clock_timestamp(), updated_at = clock_timestamp()
             WHERE id = v_attempt.id;
        END IF;
        UPDATE agent_model_steps SET status = 'cancelled',
               stop_reason = 'cancelled', terminal_reason = 'run_cancelled',
               state_version = state_version + 1,
               completed_at = clock_timestamp(), updated_at = clock_timestamp()
         WHERE id = v_step.id;
    END IF;
END;
$$;

CREATE FUNCTION get_agent_action(p_action_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_action agent_actions%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'action', to_jsonb(v_action),
        'attempt', (SELECT to_jsonb(attempt) FROM agent_action_attempts attempt
                     WHERE attempt.action_id = v_action.id
                     ORDER BY attempt_number DESC LIMIT 1),
        'result', (SELECT to_jsonb(result) FROM agent_action_results result
                    WHERE result.action_id = v_action.id));
END;
$$;

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
        RETURN jsonb_build_object('outcome', 'already_cancelled',
                                  'entity_id', v_run.id,
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
    mark_agent_action_accepted(UUID, UUID, BIGINT, TEXT, JSONB),
    record_agent_action_unknown(UUID, UUID, BIGINT, TEXT, JSONB),
    claim_agent_action_reconciliation(UUID, BIGINT, TEXT, INTEGER),
    renew_agent_action_reconciliation(UUID, UUID, BIGINT, INTEGER),
    resolve_agent_action_reconciliation(
        UUID, UUID, BIGINT, TEXT, TEXT, JSONB, JSONB),
    get_agent_action(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION _cancel_agent_run_action_work(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    mark_agent_action_accepted(UUID, UUID, BIGINT, TEXT, JSONB),
    record_agent_action_unknown(UUID, UUID, BIGINT, TEXT, JSONB),
    claim_agent_action_reconciliation(UUID, BIGINT, TEXT, INTEGER),
    renew_agent_action_reconciliation(UUID, UUID, BIGINT, INTEGER),
    resolve_agent_action_reconciliation(
        UUID, UUID, BIGINT, TEXT, TEXT, JSONB, JSONB),
    get_agent_action(UUID)
TO everydayai_worker;

RESET ROLE;
