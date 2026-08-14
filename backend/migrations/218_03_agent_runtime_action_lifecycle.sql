-- 218_03: Action claim, fencing, dispatch, and terminal result lifecycle.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION claim_ready_agent_actions(
    p_worker_id TEXT, p_claim_request_id TEXT,
    p_batch_size INTEGER DEFAULT 10,
    p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_rows JSONB;
    v_batch agent_action_claim_batches%ROWTYPE;
    v_created BOOLEAN := FALSE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NULLIF(btrim(p_worker_id), '') IS NULL
       OR NULLIF(btrim(p_claim_request_id), '') IS NULL
       OR length(btrim(p_claim_request_id)) > 200
       OR p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'AGENT_ACTION_CLAIM_INVALID' USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_action_claim_batches(
        claim_request_id, worker_id, batch_size, lease_seconds
    ) VALUES (
        btrim(p_claim_request_id), btrim(p_worker_id),
        p_batch_size, p_lease_seconds
    ) ON CONFLICT DO NOTHING RETURNING * INTO v_batch;
    v_created := FOUND;
    IF NOT v_created THEN
        SELECT * INTO v_batch FROM agent_action_claim_batches
         WHERE claim_request_id = btrim(p_claim_request_id) FOR UPDATE;
        IF v_batch.worker_id IS DISTINCT FROM btrim(p_worker_id)
           OR v_batch.batch_size IS DISTINCT FROM p_batch_size
           OR v_batch.lease_seconds IS DISTINCT FROM p_lease_seconds THEN
            RETURN jsonb_build_object('outcome', 'claim_request_conflict');
        END IF;
        SELECT COALESCE(jsonb_agg(to_jsonb(attempt) ORDER BY claimed_at, id), '[]')
          INTO v_rows FROM agent_action_attempts attempt
         WHERE attempt.claim_request_id = v_batch.claim_request_id;
        RETURN jsonb_build_object('outcome', 'claimed', 'attempts', v_rows);
    END IF;
    WITH candidates AS (
        SELECT action.id
          FROM agent_actions action
          JOIN agent_runs run ON run.id = action.run_id
         WHERE action.status = 'queued'
           AND action.policy_decision = 'preauthorized'
           AND run.status IN ('running', 'waiting_actions')
           AND NOT EXISTS (
               SELECT 1 FROM unnest(action.dependency_ids) dependency_id
               LEFT JOIN agent_actions dependency ON dependency.id = dependency_id
               LEFT JOIN agent_action_results result
                      ON result.action_id = dependency.id
                WHERE dependency.id IS NULL OR result.action_id IS NULL
           )
         ORDER BY action.created_at, action.id
         FOR UPDATE OF action SKIP LOCKED LIMIT p_batch_size
    ), updated AS (
        UPDATE agent_actions action SET status = 'running',
               started_at = COALESCE(started_at, clock_timestamp()),
               state_version = state_version + 1,
               updated_at = clock_timestamp()
          FROM candidates WHERE action.id = candidates.id
        RETURNING action.*
    ), attempts AS (
        INSERT INTO agent_action_attempts(
            action_id, session_id, run_id, org_id, user_id, attempt_number,
            status, dispatch_phase, worker_id, claim_request_id, execution_token,
            lease_expires_at, idempotency_key, request_hash, retry_disposition
        )
        SELECT action.id, action.session_id, action.run_id, action.org_id,
               action.user_id,
               COALESCE((SELECT max(old.attempt_number)
                           FROM agent_action_attempts old
                          WHERE old.action_id = action.id), 0) + 1,
               'claimed', 'claimed', btrim(p_worker_id),
               v_batch.claim_request_id, gen_random_uuid(),
               clock_timestamp() + make_interval(secs => p_lease_seconds),
               'action:' || action.id::TEXT || ':attempt:' ||
               (COALESCE((SELECT max(old.attempt_number)
                            FROM agent_action_attempts old
                           WHERE old.action_id = action.id), 0) + 1)::TEXT,
               action.request_hash, action.retry_disposition
          FROM updated action
        RETURNING *
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(attempts) ORDER BY claimed_at, id), '[]')
      INTO v_rows FROM attempts;
    RETURN jsonb_build_object('outcome', 'claimed', 'attempts', v_rows);
END;
$$;

CREATE FUNCTION get_agent_action_claim_batch(
    p_worker_id TEXT, p_claim_request_id TEXT
)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_batch agent_action_claim_batches%ROWTYPE; v_rows JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_batch FROM agent_action_claim_batches
     WHERE claim_request_id = btrim(p_claim_request_id);
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF v_batch.worker_id IS DISTINCT FROM btrim(p_worker_id) THEN
        RETURN jsonb_build_object('outcome', 'claim_request_conflict');
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(attempt) ORDER BY claimed_at, id), '[]')
      INTO v_rows FROM agent_action_attempts attempt
     WHERE attempt.claim_request_id = v_batch.claim_request_id;
    RETURN jsonb_build_object('outcome', 'found', 'attempts', v_rows);
END;
$$;

CREATE FUNCTION renew_agent_action_attempt(
    p_attempt_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF v_attempt.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.status NOT IN ('claimed', 'dispatching')
       OR v_attempt.state_version <> p_expected_state_version
       OR v_attempt.lease_expires_at <= clock_timestamp()
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_action_attempts SET
           lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'renewed', 'attempt_id', v_attempt.id,
        'state_version', v_attempt.state_version,
        'lease_expires_at', v_attempt.lease_expires_at);
END;
$$;

CREATE FUNCTION mark_agent_action_dispatching(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_state_version BIGINT, p_request_hash TEXT
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
    IF v_attempt.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.status = 'dispatching' THEN
        RETURN jsonb_build_object('outcome', 'already_dispatching',
                                  'state_version', v_attempt.state_version);
    END IF;
    IF v_attempt.status <> 'claimed'
       OR v_attempt.state_version <> p_expected_state_version
       OR v_action.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_action_attempts SET status = 'dispatching',
           dispatch_phase = 'request_started', dispatched_at = clock_timestamp(),
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object('outcome', 'dispatching',
                              'state_version', v_attempt.state_version);
END;
$$;

CREATE FUNCTION recover_expired_agent_action_attempt(
    p_attempt_id UUID, p_expected_state_version BIGINT,
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_action_attempts%ROWTYPE;
    v_action agent_actions%ROWTYPE;
    v_new agent_action_attempts%ROWTYPE;
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
    IF v_attempt.state_version <> p_expected_state_version
       OR v_attempt.status NOT IN ('claimed', 'dispatching')
       OR v_attempt.lease_expires_at > clock_timestamp()
       OR v_action.status <> 'running'
       OR NULLIF(btrim(p_worker_id), '') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_attempt.status = 'dispatching' THEN
        UPDATE agent_action_attempts SET status = 'unknown',
               ambiguity_evidence = jsonb_build_object(
                   'kind', 'lease_expired_after_dispatch'),
               retry_disposition = 'retry_after_reconcile',
               state_version = state_version + 1,
               updated_at = clock_timestamp()
         WHERE id = v_attempt.id RETURNING * INTO v_attempt;
        UPDATE agent_actions SET status = 'unknown',
               retry_disposition = 'retry_after_reconcile',
               state_version = state_version + 1,
               updated_at = clock_timestamp() WHERE id = v_action.id;
        PERFORM append_agent_runtime_event(
            v_action.session_id, 'action.unknown', v_action.run_id,
            v_action.model_step_id, v_action.id, 'system', session_user,
            jsonb_build_object('action_id', v_action.id,
                               'kind', 'lease_expired_after_dispatch'),
            ARRAY['web_runtime', 'audit']::TEXT[]);
        RETURN jsonb_build_object(
            'outcome', 'unknown', 'attempt_id', v_attempt.id,
            'state_version', v_attempt.state_version);
    END IF;
    IF v_action.retry_disposition <> 'retry_safe' THEN
        RETURN jsonb_build_object('outcome', 'not_reconcilable');
    END IF;
    UPDATE agent_action_attempts SET status = 'failed',
           state_version = state_version + 1, ended_at = clock_timestamp(),
           updated_at = clock_timestamp() WHERE id = v_attempt.id;
    INSERT INTO agent_action_attempts(
        action_id,session_id,run_id,org_id,user_id,attempt_number,status,
        dispatch_phase,worker_id,execution_token,lease_expires_at,
        idempotency_key,request_hash,retry_disposition
    ) VALUES (
        v_action.id,v_action.session_id,v_action.run_id,v_action.org_id,
        v_action.user_id,v_attempt.attempt_number+1,'claimed','claimed',
        btrim(p_worker_id),gen_random_uuid(),
        clock_timestamp()+make_interval(secs=>p_lease_seconds),
        'action:'||v_action.id::TEXT||':attempt:'||
            (v_attempt.attempt_number+1)::TEXT,
        v_action.request_hash,v_action.retry_disposition
    ) RETURNING * INTO v_new;
    RETURN jsonb_build_object(
        'outcome','claimed','action_id',v_action.id,'attempt_id',v_new.id,
        'execution_token',v_new.execution_token,
        'lease_expires_at',v_new.lease_expires_at,
        'state_version',v_new.state_version);
END;
$$;

CREATE FUNCTION _finish_agent_action(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT,
    p_action_status TEXT, p_result JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_action_attempts%ROWTYPE; v_action agent_actions%ROWTYPE;
    v_run agent_runs%ROWTYPE; v_session agent_runtime_sessions%ROWTYPE;
    v_existing agent_action_results%ROWTYPE; v_event JSONB; v_wake JSONB; v_result_hash TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id=v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    SELECT * INTO v_action FROM agent_actions WHERE id=v_attempt.action_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    SELECT * INTO v_existing FROM agent_action_results
     WHERE action_id=v_action.id FOR UPDATE;
    v_result_hash := _agent_action_result_hash(p_result,p_action_status,
        v_session.conversation_id,v_action.org_id);
    IF FOUND OR v_action.status IN ('completed', 'failed') THEN
        IF NOT FOUND OR v_action.status IS DISTINCT FROM p_action_status
           OR v_existing.result_hash IS DISTINCT FROM v_result_hash THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object('outcome','already_'||p_action_status,
                                  'action_id',v_action.id);
    END IF;
    IF v_run.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome','run_cancelled');
    END IF;
    IF (
        v_attempt.status IN ('accepted', 'unknown')
        AND (
            v_attempt.reconciliation_token IS DISTINCT FROM p_execution_token
            OR v_attempt.reconciliation_lease_expires_at <= clock_timestamp()
        )
    ) OR (
        v_attempt.status NOT IN ('accepted', 'unknown')
        AND v_attempt.execution_token IS DISTINCT FROM p_execution_token
    ) THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF (v_attempt.status NOT IN ('accepted', 'unknown')
        AND v_attempt.lease_expires_at <= clock_timestamp())
       OR v_attempt.state_version <> p_expected_attempt_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.status NOT IN ('claimed', 'dispatching', 'accepted', 'unknown')
       OR v_action.status NOT IN ('running', 'accepted', 'unknown') THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_attempt.status = 'claimed' AND p_action_status <> 'failed' THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    INSERT INTO agent_action_results(
        action_id, session_id, run_id, org_id, user_id, status, result_hash,
        summary, data, artifact_ids, usage, cost, external_receipt, error_code
    ) VALUES (
        v_action.id, v_action.session_id, v_action.run_id, v_action.org_id,
        v_action.user_id, p_result->>'status', v_result_hash,
        COALESCE(p_result->>'summary', ''), p_result->'data',
        ARRAY(SELECT value::UUID FROM jsonb_array_elements_text(
            COALESCE(p_result->'artifact_ids', '[]'::JSONB)) value),
        COALESCE(p_result->'usage', '{}'::JSONB),
        COALESCE(p_result->'cost', '{}'::JSONB),
        COALESCE(p_result->'external_receipt', '{}'::JSONB),
        p_result->>'error_code'
    );
    UPDATE agent_action_attempts SET status = p_action_status,
           state_version = state_version + 1, ended_at = clock_timestamp(),
           reconciliation_token = NULL,
           reconciliation_lease_expires_at = NULL,
           updated_at = clock_timestamp()
     WHERE id = v_attempt.id RETURNING * INTO v_attempt;
    UPDATE agent_actions SET status = p_action_status,
           terminal_reason = CASE WHEN p_action_status = 'failed'
               THEN p_result->>'error_code' ELSE NULL END,
           state_version = state_version + 1, completed_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE id = v_action.id RETURNING * INTO v_action;
    IF v_action.blocking THEN
        IF v_run.blocking_action_count <= 0 THEN
            RAISE EXCEPTION 'AGENT_ACTION_BLOCKER_UNDERFLOW'
                USING ERRCODE = '55000';
        END IF;
        UPDATE agent_runs SET
               blocking_action_count = blocking_action_count - 1,
               state_version = state_version + 1,
               updated_at = clock_timestamp()
         WHERE id = v_run.id RETURNING * INTO v_run;
    END IF;
    v_event := append_agent_runtime_event(
        v_action.session_id, 'action.' || p_action_status, v_action.run_id,
        v_action.model_step_id, v_action.id, 'executor', session_user,
        jsonb_build_object('action_id', v_action.id,
                           'result_hash', v_result_hash),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    IF v_action.blocking AND v_run.blocking_action_count = 0
       AND v_run.status = 'waiting_actions' THEN
        UPDATE agent_runs SET status = 'queued',
               state_version = state_version + 1, updated_at = clock_timestamp()
         WHERE id = v_run.id RETURNING * INTO v_run;
        v_wake := append_agent_runtime_event(
            v_run.session_id, 'run.resumed', v_run.id, v_action.model_step_id,
            v_action.id, 'system', session_user, '{}'::JSONB,
            ARRAY['web_runtime', 'audit']::TEXT[]);
    END IF;
    RETURN jsonb_build_object('outcome',p_action_status,'action_id',v_action.id,
        'result_hash',v_result_hash,'blocking_action_count',
        v_run.blocking_action_count,'run_status',v_run.status,'event_sequence',
        v_event->'event_sequence','wake_event_sequence',v_wake->'event_sequence');
END;
$$;

CREATE FUNCTION fail_claimed_agent_action(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT, p_error_code TEXT
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
    IF v_attempt.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.lease_expires_at <= clock_timestamp()
       OR v_attempt.state_version <> p_expected_attempt_version
       OR v_attempt.request_hash IS DISTINCT FROM p_request_hash
       OR v_attempt.status <> 'claimed' OR v_action.status <> 'running'
       OR v_action.retry_disposition <> 'retry_safe'
       OR NULLIF(btrim(p_error_code), '') IS NULL THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_action_attempts SET status = 'failed',
           state_version = state_version + 1, ended_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    UPDATE agent_actions SET status = 'queued',
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = v_action.id RETURNING * INTO v_action;
    PERFORM append_agent_runtime_event(
        v_action.session_id, 'action.retry_scheduled', v_action.run_id,
        v_action.model_step_id, v_action.id, 'executor', session_user,
        jsonb_build_object('action_id', v_action.id,
                           'error_code', LEFT(p_error_code, 200)),
        ARRAY['audit']::TEXT[]);
    RETURN jsonb_build_object(
        'outcome', 'failed', 'action_id', v_action.id,
        'attempt_id', v_attempt.id, 'action_status', v_action.status,
        'state_version', v_attempt.state_version);
END;
$$;

CREATE FUNCTION complete_agent_action(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT, p_result JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    RETURN _finish_agent_action(
        p_attempt_id, p_execution_token, p_expected_attempt_version,
        p_request_hash, 'completed', p_result);
END;
$$;

CREATE FUNCTION fail_agent_action(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT, p_result JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    RETURN _finish_agent_action(
        p_attempt_id, p_execution_token, p_expected_attempt_version,
        p_request_hash, 'failed', p_result);
END;
$$;

REVOKE ALL ON FUNCTION
    claim_ready_agent_actions(TEXT, TEXT, INTEGER, INTEGER),
    get_agent_action_claim_batch(TEXT, TEXT),
    renew_agent_action_attempt(UUID, UUID, BIGINT, INTEGER),
    mark_agent_action_dispatching(UUID, UUID, BIGINT, TEXT),
    recover_expired_agent_action_attempt(UUID, BIGINT, TEXT, INTEGER),
    _finish_agent_action(UUID, UUID, BIGINT, TEXT, TEXT, JSONB),
    fail_claimed_agent_action(UUID, UUID, BIGINT, TEXT, TEXT),
    complete_agent_action(UUID, UUID, BIGINT, TEXT, JSONB),
    fail_agent_action(UUID, UUID, BIGINT, TEXT, JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    claim_ready_agent_actions(TEXT, TEXT, INTEGER, INTEGER),
    get_agent_action_claim_batch(TEXT, TEXT),
    renew_agent_action_attempt(UUID, UUID, BIGINT, INTEGER),
    mark_agent_action_dispatching(UUID, UUID, BIGINT, TEXT),
    recover_expired_agent_action_attempt(UUID, BIGINT, TEXT, INTEGER),
    fail_claimed_agent_action(UUID, UUID, BIGINT, TEXT, TEXT),
    complete_agent_action(UUID, UUID, BIGINT, TEXT, JSONB),
    fail_agent_action(UUID, UUID, BIGINT, TEXT, JSONB)
TO everydayai_worker;

RESET ROLE;
