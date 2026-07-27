-- 220_02: Worker-only Run discovery and aggregate recovery snapshots.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_claimed_agent_run(p_worker_id TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_attempt agent_run_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT run.* INTO v_run
      FROM agent_run_attempts attempt
      JOIN agent_runs run ON run.id = attempt.run_id
     WHERE attempt.worker_id = BTRIM(p_worker_id)
       AND attempt.ended_at IS NULL
       AND run.status = 'running'
       AND run.execution_token = attempt.execution_token
       AND run.lease_expires_at > clock_timestamp()
     ORDER BY attempt.claimed_at DESC, attempt.id LIMIT 1;
    IF v_run.id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    SELECT * INTO v_attempt FROM agent_run_attempts
     WHERE run_id = v_run.id
       AND execution_token = v_run.execution_token
       AND worker_id = BTRIM(p_worker_id)
       AND ended_at IS NULL;
    RETURN jsonb_build_object(
        'outcome', 'found', 'entity_id', v_run.id,
        'execution_token', v_attempt.execution_token,
        'state_version', v_run.state_version,
        'lease_expires_at', v_run.lease_expires_at);
END;
$$;

CREATE FUNCTION claim_next_agent_run(
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_candidate RECORD;
    v_run agent_runs%ROWTYPE;
    v_receipt JSONB;
    v_event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NULLIF(BTRIM(p_worker_id), '') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 300
       OR p_max_attempts NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'AGENT_RUN_SCAN_INVALID' USING ERRCODE = '22023';
    END IF;
    v_receipt := get_claimed_agent_run(p_worker_id);
    IF v_receipt->>'outcome' = 'found' THEN
        RETURN v_receipt || jsonb_build_object('outcome', 'claimed');
    END IF;
    FOR v_candidate IN
        SELECT run.id, run.session_id
          FROM agent_runs run
         WHERE run.status = 'queued'
            OR (run.status = 'running'
                AND run.lease_expires_at <= clock_timestamp())
         ORDER BY run.created_at, run.id
         LIMIT 100
    LOOP
        PERFORM 1 FROM agent_runtime_sessions
         WHERE id = v_candidate.session_id FOR UPDATE;
        SELECT * INTO v_run FROM agent_runs
         WHERE id = v_candidate.id FOR UPDATE SKIP LOCKED;
        IF NOT FOUND THEN CONTINUE; END IF;
        IF v_run.status = 'queued'
           OR (v_run.status = 'running'
               AND v_run.lease_expires_at <= clock_timestamp()) THEN
            IF v_run.attempt_count >= p_max_attempts THEN
                UPDATE agent_run_attempts SET ended_at = clock_timestamp(),
                       outcome = 'lease_lost'
                 WHERE run_id = v_run.id AND ended_at IS NULL;
                UPDATE agent_runs SET status = 'failed',
                       execution_token = NULL, lease_expires_at = NULL,
                       completed_at = clock_timestamp(),
                       terminal_reason = 'attempts_exhausted',
                       state_version = state_version + 1,
                       updated_at = clock_timestamp()
                 WHERE id = v_run.id RETURNING * INTO v_run;
                v_event := append_agent_runtime_event(
                    v_run.session_id, 'run.failed', v_run.id, NULL,
                    gen_random_uuid(), 'system', p_worker_id,
                    jsonb_build_object('reason', 'attempts_exhausted'),
                    ARRAY['web_runtime', 'audit']::TEXT[]);
                RETURN jsonb_build_object(
                    'outcome', 'attempts_exhausted',
                    'entity_id', v_run.id,
                    'state_version', v_run.state_version,
                    'event_sequence', v_event->'event_sequence');
            END IF;
            v_receipt := claim_agent_run(
                v_run.id, BTRIM(p_worker_id),
                p_lease_seconds, p_max_attempts);
            IF v_receipt->>'outcome' <> 'busy' THEN
                RETURN v_receipt;
            END IF;
        END IF;
    END LOOP;
    RETURN jsonb_build_object('outcome', 'not_found');
END;
$$;

CREATE FUNCTION renew_model_attempt_execution(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_attempt_execution_token UUID, p_expected_state_version BIGINT,
    p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_model_attempts%ROWTYPE; v_run agent_runs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs
     WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps
     WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_attempt_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp()
       OR v_attempt.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.status <> 'dispatching'
       OR v_attempt.state_version <> p_expected_state_version
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_model_attempts SET
           lease_expires_at = clock_timestamp()
               + make_interval(secs => p_lease_seconds),
           state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'renewed', 'attempt_id', v_attempt.id,
        'state_version', v_attempt.state_version,
        'lease_expires_at', v_attempt.lease_expires_at);
END;
$$;

CREATE FUNCTION get_agent_run_aggregate(
    p_run_id UUID, p_worker_id TEXT, p_execution_token UUID
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_run agent_runs%ROWTYPE;
    v_step agent_model_steps%ROWTYPE;
    v_attempt agent_model_attempts%ROWTYPE;
    v_result agent_model_results%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token
       OR v_run.lease_expires_at <= clock_timestamp()
       OR NOT EXISTS (
           SELECT 1 FROM agent_run_attempts attempt
            WHERE attempt.run_id = v_run.id
              AND attempt.execution_token = p_execution_token
              AND attempt.worker_id = BTRIM(p_worker_id)
              AND attempt.ended_at IS NULL
       ) THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE run_id = v_run.id ORDER BY step_number DESC LIMIT 1;
    IF v_step.id IS NOT NULL THEN
        SELECT * INTO v_attempt FROM agent_model_attempts
         WHERE model_step_id = v_step.id
           AND status IN ('prepared', 'dispatching', 'unknown')
         ORDER BY attempt_number DESC LIMIT 1;
        SELECT * INTO v_result FROM agent_model_results
         WHERE model_step_id = v_step.id;
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found',
        'run', to_jsonb(v_run),
        'latest_model_step', CASE WHEN v_step.id IS NULL
            THEN NULL ELSE to_jsonb(v_step) END,
        'unresolved_model_attempt', CASE WHEN v_attempt.id IS NULL
            THEN NULL ELSE to_jsonb(v_attempt) END,
        'latest_model_result', CASE WHEN v_result.id IS NULL
            THEN NULL ELSE to_jsonb(v_result) END,
        'model_steps', (
            SELECT COALESCE(jsonb_agg(to_jsonb(step)
                ORDER BY step.step_number), '[]'::JSONB)
              FROM agent_model_steps step WHERE step.run_id = v_run.id
        ),
        'actions', (
            SELECT COALESCE(jsonb_agg(
                to_jsonb(action) || jsonb_build_object(
                    'result', (SELECT to_jsonb(result)
                        FROM agent_action_results result
                        WHERE result.action_id = action.id))
                ORDER BY action.action_index, action.id), '[]'::JSONB)
              FROM agent_actions action WHERE action.run_id = v_run.id
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION
    get_claimed_agent_run(TEXT),
    claim_next_agent_run(TEXT, INTEGER, INTEGER),
    get_agent_run_aggregate(UUID, TEXT, UUID),
    renew_model_attempt_execution(UUID, UUID, UUID, BIGINT, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    get_claimed_agent_run(TEXT),
    claim_next_agent_run(TEXT, INTEGER, INTEGER),
    get_agent_run_aggregate(UUID, TEXT, UUID),
    renew_model_attempt_execution(UUID, UUID, UUID, BIGINT, INTEGER)
TO everydayai_worker;

RESET ROLE;
