-- 219_02a: Fence historical Commands by their linked Run state.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_command_run_eligibility(
    p_command agent_session_commands
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE;
BEGIN
    IF p_command.result_entity_id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'pending');
    END IF;
    SELECT * INTO v_run FROM agent_runs
     WHERE id = p_command.result_entity_id FOR UPDATE;
    IF NOT FOUND
       OR v_run.command_id IS DISTINCT FROM p_command.id
       OR v_run.session_id IS DISTINCT FROM p_command.session_id
       OR v_run.org_id IS DISTINCT FROM p_command.org_id
       OR v_run.user_id IS DISTINCT FROM p_command.user_id THEN
        RETURN jsonb_build_object('outcome', 'association_rejected');
    END IF;
    IF v_run.status = 'queued'
       OR (
           v_run.status = 'running'
           AND v_run.lease_expires_at <= clock_timestamp()
       ) THEN
        RETURN jsonb_build_object(
            'outcome', 'pending', 'run_status', v_run.status);
    END IF;
    IF v_run.status = 'running' THEN
        RETURN jsonb_build_object('outcome', 'busy');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'already_processed', 'run_status', v_run.status);
END;
$$;

CREATE FUNCTION _close_nonexecuting_agent_command(
    p_command agent_session_commands, p_session agent_runtime_sessions,
    p_worker_id TEXT, p_run_status TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_status TEXT := CASE
        WHEN p_run_status IN ('failed', 'cancelled') THEN 'failed'
        ELSE 'completed'
    END;
    v_error TEXT := CASE
        WHEN p_run_status IN ('failed', 'cancelled')
        THEN 'run_' || p_run_status
    END;
BEGIN
    INSERT INTO agent_command_claims(
        command_id, session_id, org_id, user_id, scope_kind, scope_id,
        worker_id, fencing_token, lease_expires_at, attempt_number, status,
        outcome, error_class, finished_at, run_id
    ) VALUES (
        p_command.id, p_session.id, p_session.org_id, p_session.user_id,
        p_session.scope_kind, p_session.scope_id, BTRIM(p_worker_id),
        gen_random_uuid(), clock_timestamp(), 1, v_status, v_status, v_error,
        clock_timestamp(), p_command.result_entity_id
    ) ON CONFLICT (command_id) DO UPDATE SET
        status = EXCLUDED.status, outcome = EXCLUDED.outcome,
        error_class = EXCLUDED.error_class, run_id = EXCLUDED.run_id,
        finished_at = clock_timestamp(), updated_at = clock_timestamp();
    RETURN jsonb_build_object(
        'outcome', 'already_processed', 'command_id', p_command.id,
        'run_id', p_command.result_entity_id, 'run_status', p_run_status);
END;
$$;

CREATE FUNCTION _claim_eligible_agent_command(
    p_command agent_session_commands, p_session agent_runtime_sessions,
    p_claim agent_command_claims, p_worker_id TEXT, p_lease_seconds INTEGER,
    p_max_attempts INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_claim agent_command_claims%ROWTYPE;
    v_envelope JSONB;
    v_target_id UUID;
BEGIN
    IF p_claim.command_id IS NOT NULL
       AND p_claim.attempt_number >= p_max_attempts THEN
        RETURN _finish_exhausted_agent_command(p_command, p_claim);
    END IF;
    v_envelope := _agent_command_run_envelope(p_command);
    IF v_envelope IS NULL OR p_command.request_hash IS DISTINCT FROM md5(
        jsonb_build_object(
            'command_type', p_command.command_type,
            'payload', p_command.payload
        )::TEXT
    ) THEN
        RETURN _reject_agent_command(
            p_command, p_session, p_worker_id, 'scope_rejected');
    END IF;
    IF p_command.command_type = 'cancel' THEN
        BEGIN
            v_target_id := (p_command.payload->>'target_run_id')::UUID;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN _reject_agent_command(
                p_command, p_session, p_worker_id, 'scope_rejected');
        END;
        IF v_target_id IS NULL THEN
            RETURN _reject_agent_command(
                p_command, p_session, p_worker_id, 'scope_rejected');
        END IF;
    END IF;
    INSERT INTO agent_command_claims(
        command_id, session_id, org_id, user_id, scope_kind, scope_id,
        worker_id, fencing_token, lease_expires_at, attempt_number, status
    ) VALUES (
        p_command.id, p_session.id, p_session.org_id, p_session.user_id,
        p_session.scope_kind, p_session.scope_id, BTRIM(p_worker_id),
        gen_random_uuid(),
        clock_timestamp() + make_interval(secs => p_lease_seconds), 1, 'claimed'
    ) ON CONFLICT (command_id) DO UPDATE SET
        worker_id = EXCLUDED.worker_id, fencing_token = EXCLUDED.fencing_token,
        lease_expires_at = EXCLUDED.lease_expires_at,
        attempt_number = agent_command_claims.attempt_number + 1,
        status = 'claimed', outcome = NULL, error_class = NULL,
        claimed_at = clock_timestamp(), finished_at = NULL,
        updated_at = clock_timestamp()
    RETURNING * INTO v_claim;
    RETURN _ensure_agent_command_run(
        p_command, p_session, v_claim, v_envelope, v_target_id);
END;
$$;

CREATE OR REPLACE FUNCTION claim_pending_agent_command_and_ensure_run(
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_command agent_session_commands%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_claim agent_command_claims%ROWTYPE;
    v_eligibility JSONB;
    v_candidate RECORD;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NULLIF(BTRIM(p_worker_id), '') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 300
       OR p_max_attempts NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'AGENT_COMMAND_CLAIM_INVALID' USING ERRCODE = '22023';
    END IF;
    FOR v_candidate IN
        SELECT command.id, command.session_id
          FROM agent_session_commands command
          LEFT JOIN agent_command_claims claim ON claim.command_id = command.id
          LEFT JOIN agent_runs run ON run.id = command.result_entity_id
         WHERE (
             claim.command_id IS NULL
             OR (claim.status = 'claimed'
                 AND claim.lease_expires_at <= clock_timestamp())
           )
           AND NOT (
               run.id IS NOT NULL
               AND run.command_id = command.id
               AND run.session_id = command.session_id
               AND run.org_id IS NOT DISTINCT FROM command.org_id
               AND run.user_id IS NOT DISTINCT FROM command.user_id
               AND run.status = 'running'
               AND run.lease_expires_at > clock_timestamp()
           )
         ORDER BY (command.command_type = 'cancel') DESC,
                  command.created_at, command.id
         LIMIT 100
    LOOP
        SELECT * INTO v_session FROM agent_runtime_sessions
         WHERE id = v_candidate.session_id FOR UPDATE;
        SELECT * INTO v_command FROM agent_session_commands
         WHERE id = v_candidate.id FOR UPDATE SKIP LOCKED;
        IF NOT FOUND THEN CONTINUE; END IF;
        SELECT * INTO v_claim FROM agent_command_claims
         WHERE command_id = v_command.id FOR UPDATE;
        IF FOUND AND (
            v_claim.status <> 'claimed'
            OR v_claim.lease_expires_at > clock_timestamp()
        ) THEN
            v_command.id := NULL;
            CONTINUE;
        END IF;
        v_eligibility := _agent_command_run_eligibility(v_command);
        IF v_eligibility->>'outcome' = 'busy' THEN
            v_command.id := NULL;
            CONTINUE;
        END IF;
        EXIT;
    END LOOP;
    IF v_command.id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_session.id IS NULL
       OR v_session.org_id IS DISTINCT FROM v_command.org_id
       OR v_session.user_id IS DISTINCT FROM v_command.user_id THEN
        RETURN _reject_agent_command(
            v_command, v_session, p_worker_id, 'scope_rejected');
    END IF;
    IF v_eligibility->>'outcome' = 'association_rejected' THEN
        RETURN _reject_agent_command(
            v_command, v_session, p_worker_id, 'association_rejected');
    END IF;
    IF v_eligibility->>'outcome' = 'already_processed' THEN
        RETURN _close_nonexecuting_agent_command(
            v_command, v_session, p_worker_id,
            v_eligibility->>'run_status');
    END IF;
    RETURN _claim_eligible_agent_command(
        v_command, v_session, v_claim, p_worker_id, p_lease_seconds,
        p_max_attempts);
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_command_run_eligibility(agent_session_commands),
    _close_nonexecuting_agent_command(
        agent_session_commands, agent_runtime_sessions, TEXT, TEXT),
    _claim_eligible_agent_command(
        agent_session_commands, agent_runtime_sessions,
        agent_command_claims, TEXT, INTEGER, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
