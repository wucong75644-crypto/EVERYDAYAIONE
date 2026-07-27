SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION claim_pending_agent_command_and_ensure_run(
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_command agent_session_commands%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_claim agent_command_claims%ROWTYPE;
    v_envelope JSONB;
    v_token UUID := gen_random_uuid();
    v_target_id UUID;
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
         WHERE claim.command_id IS NULL
            OR (
                claim.status = 'claimed'
                AND claim.lease_expires_at <= clock_timestamp()
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
        IF (
            FOUND AND (
                v_claim.status <> 'claimed'
                OR v_claim.lease_expires_at > clock_timestamp()
            )
        ) THEN
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
    SELECT * INTO v_claim FROM agent_command_claims
     WHERE command_id = v_command.id FOR UPDATE;
    IF FOUND AND v_claim.status = 'claimed'
       AND v_claim.lease_expires_at > clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'already_claimed');
    END IF;
    IF FOUND AND v_claim.attempt_number >= p_max_attempts THEN
        RETURN _finish_exhausted_agent_command(v_command, v_claim);
    END IF;
    v_envelope := _agent_command_run_envelope(v_command);
    IF v_envelope IS NULL OR v_command.request_hash IS DISTINCT FROM md5(
        jsonb_build_object(
            'command_type', v_command.command_type,
            'payload', v_command.payload
        )::TEXT
    ) THEN
        RETURN _reject_agent_command(
            v_command, v_session, p_worker_id, 'scope_rejected');
    END IF;
    IF v_command.command_type = 'cancel' THEN
        BEGIN
            v_target_id := (v_command.payload->>'target_run_id')::UUID;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN _reject_agent_command(
                v_command, v_session, p_worker_id, 'scope_rejected');
        END;
        IF v_target_id IS NULL THEN
            RETURN _reject_agent_command(
                v_command, v_session, p_worker_id, 'scope_rejected');
        END IF;
    END IF;
    INSERT INTO agent_command_claims(
        command_id, session_id, org_id, user_id, scope_kind, scope_id,
        worker_id, fencing_token, lease_expires_at, attempt_number, status
    ) VALUES (
        v_command.id, v_session.id, v_session.org_id, v_session.user_id,
        v_session.scope_kind, v_session.scope_id, BTRIM(p_worker_id), v_token,
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
        v_command, v_session, v_claim, v_envelope, v_target_id);
END;
$$;

DROP FUNCTION _claim_eligible_agent_command(
    agent_session_commands, agent_runtime_sessions,
    agent_command_claims, TEXT, INTEGER, INTEGER);
DROP FUNCTION _close_nonexecuting_agent_command(
    agent_session_commands, agent_runtime_sessions, TEXT, TEXT);
DROP FUNCTION _agent_command_run_eligibility(agent_session_commands);

RESET ROLE;
