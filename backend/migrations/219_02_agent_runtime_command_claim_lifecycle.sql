-- 219_02: Narrow Worker RPCs for Command scan, claim, recovery and finish.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_command_run_envelope(p_command agent_session_commands)
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_envelope JSONB := p_command.payload->'run_envelope';
BEGIN
    IF jsonb_typeof(v_envelope) IS DISTINCT FROM 'object'
       OR v_envelope = '{}'::JSONB
       OR v_envelope->>'run_kind' NOT IN ('user', 'continuation')
       OR jsonb_typeof(v_envelope->'context_receipt') IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_envelope->'config_snapshot') IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_envelope->'capability_snapshot') IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_envelope->'request_identity') IS DISTINCT FROM 'object'
       OR v_envelope->'context_receipt' = '{}'::JSONB
       OR v_envelope->'config_snapshot' = '{}'::JSONB
       OR v_envelope->'capability_snapshot' = '{}'::JSONB
       OR v_envelope->'request_identity'->>'session_id'
            IS DISTINCT FROM p_command.session_id::TEXT
       OR v_envelope->'request_identity'->>'idempotency_key'
            IS DISTINCT FROM p_command.idempotency_key
       OR pg_column_size(v_envelope) > 262144 THEN
        RETURN NULL;
    END IF;
    RETURN v_envelope;
END;
$$;

CREATE FUNCTION _finish_exhausted_agent_command(
    p_command agent_session_commands, p_claim agent_command_claims
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    UPDATE agent_command_claims SET status = 'attempts_exhausted',
           outcome = 'attempts_exhausted', error_class = 'attempts_exhausted',
           finished_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE command_id = p_command.id;
    PERFORM append_agent_runtime_event(
        p_command.session_id, 'command.attempts_exhausted', p_claim.run_id,
        NULL, p_command.id, 'system', session_user,
        jsonb_build_object('command_id', p_command.id,
                           'attempt_number', p_claim.attempt_number),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    RETURN jsonb_build_object(
        'outcome', 'attempts_exhausted', 'command_id', p_command.id);
END;
$$;

CREATE FUNCTION _reject_agent_command(
    p_command agent_session_commands, p_session agent_runtime_sessions,
    p_worker_id TEXT, p_error_class TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    INSERT INTO agent_command_claims(
        command_id, session_id, org_id, user_id, scope_kind, scope_id,
        worker_id, fencing_token, lease_expires_at, attempt_number, status,
        outcome, error_class, finished_at
    ) VALUES (
        p_command.id, p_session.id, p_session.org_id, p_session.user_id,
        p_session.scope_kind, p_session.scope_id, BTRIM(p_worker_id),
        gen_random_uuid(), clock_timestamp(), 1, 'failed', 'failed',
        p_error_class, clock_timestamp()
    ) ON CONFLICT (command_id) DO UPDATE SET
        worker_id = EXCLUDED.worker_id, fencing_token = EXCLUDED.fencing_token,
        lease_expires_at = EXCLUDED.lease_expires_at,
        attempt_number = agent_command_claims.attempt_number + 1,
        status = 'failed', outcome = 'failed',
        error_class = EXCLUDED.error_class,
        finished_at = clock_timestamp(), updated_at = clock_timestamp();
    RETURN jsonb_build_object(
        'outcome', p_error_class, 'command_id', p_command.id);
END;
$$;

CREATE FUNCTION _ensure_agent_command_run(
    p_command agent_session_commands, p_session agent_runtime_sessions,
    p_claim agent_command_claims, p_envelope JSONB, p_target_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_cancel JSONB;
BEGIN
    IF p_command.command_type = 'cancel' THEN
        SELECT * INTO v_run FROM agent_runs
         WHERE id = p_target_id AND session_id = p_session.id FOR UPDATE;
        IF FOUND THEN
            v_cancel := cancel_agent_run(
                v_run.id, v_run.state_version,
                COALESCE(NULLIF(p_command.payload->>'reason', ''), 'cancelled'));
            IF v_cancel->>'outcome' = 'terminal_conflict' THEN
                UPDATE agent_command_claims SET status = 'failed',
                       outcome = 'failed', error_class = 'terminal_conflict',
                       finished_at = clock_timestamp(),
                       updated_at = clock_timestamp()
                 WHERE command_id = p_command.id;
                RETURN jsonb_build_object(
                    'outcome', 'terminal_conflict',
                    'command_id', p_command.id, 'run_id', v_run.id);
            END IF;
        ELSE
            INSERT INTO agent_runs(
                id, session_id, command_id, org_id, user_id, run_kind, status,
                idempotency_key, request_hash, context_receipt,
                config_snapshot, capability_snapshot, terminal_reason,
                completed_at
            ) VALUES (
                p_target_id, p_session.id, p_command.id, p_session.org_id,
                p_session.user_id, p_envelope->>'run_kind', 'cancelled',
                p_command.id::TEXT, md5(p_envelope::TEXT),
                p_envelope->'context_receipt', p_envelope->'config_snapshot',
                p_envelope->'capability_snapshot', 'cancelled_before_start',
                clock_timestamp()
            ) RETURNING * INTO v_run;
        END IF;
    ELSE
        INSERT INTO agent_runs(
            session_id, command_id, org_id, user_id, run_kind,
            idempotency_key, request_hash, context_receipt,
            config_snapshot, capability_snapshot
        ) VALUES (
            p_session.id, p_command.id, p_session.org_id, p_session.user_id,
            p_envelope->>'run_kind', p_command.id::TEXT,
            md5(p_envelope::TEXT), p_envelope->'context_receipt',
            p_envelope->'config_snapshot', p_envelope->'capability_snapshot'
        ) ON CONFLICT (command_id) DO NOTHING;
        SELECT * INTO v_run FROM agent_runs
         WHERE command_id = p_command.id FOR UPDATE;
        IF v_run.request_hash IS DISTINCT FROM md5(p_envelope::TEXT) THEN
            UPDATE agent_command_claims SET status = 'failed',
                   outcome = 'failed', error_class = 'idempotency_conflict',
                   finished_at = clock_timestamp(),
                   updated_at = clock_timestamp()
             WHERE command_id = p_command.id;
            RETURN jsonb_build_object(
                'outcome', 'idempotency_conflict',
                'command_id', p_command.id, 'run_id', v_run.id);
        END IF;
    END IF;
    UPDATE agent_session_commands SET result_entity_id = v_run.id
     WHERE id = p_command.id AND result_entity_id IS NULL;
    UPDATE agent_command_claims SET run_id = v_run.id
     WHERE command_id = p_command.id;
    RETURN jsonb_build_object(
        'outcome', 'claimed', 'command_id', p_command.id,
        'session_id', p_session.id, 'run_id', v_run.id,
        'worker_id', p_claim.worker_id,
        'fencing_token', p_claim.fencing_token,
        'lease_expires_at', p_claim.lease_expires_at,
        'attempt_number', p_claim.attempt_number,
        'command_type', p_command.command_type);
END;
$$;

CREATE FUNCTION claim_pending_agent_command_and_ensure_run(
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
         WHERE (
                command.result_entity_id IS NULL
                AND claim.command_id IS NULL
               )
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
        ) OR (
            NOT FOUND AND v_command.result_entity_id IS NOT NULL
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

CREATE FUNCTION get_agent_command_run_claim(
    p_command_id UUID, p_worker_id TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_claim agent_command_claims%ROWTYPE; v_command agent_session_commands%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_claim FROM agent_command_claims
     WHERE (p_command_id IS NULL OR command_id = p_command_id)
       AND worker_id = BTRIM(p_worker_id)
       AND status = 'claimed'
       AND lease_expires_at > clock_timestamp()
     ORDER BY claimed_at DESC, command_id
     LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_command FROM agent_session_commands
     WHERE id = v_claim.command_id;
    RETURN jsonb_build_object(
        'outcome', 'found', 'command_id', v_claim.command_id,
        'session_id', v_claim.session_id, 'run_id', v_claim.run_id,
        'worker_id', v_claim.worker_id, 'fencing_token', v_claim.fencing_token,
        'lease_expires_at', v_claim.lease_expires_at,
        'attempt_number', v_claim.attempt_number, 'status', v_claim.status,
        'command_type', v_command.command_type);
END;
$$;

CREATE FUNCTION renew_agent_command_claim(
    p_command_id UUID, p_fencing_token UUID, p_lease_seconds INTEGER DEFAULT 90
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_claim agent_command_claims%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_claim FROM agent_command_claims
     WHERE command_id = p_command_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF v_claim.status <> 'claimed'
       OR v_claim.fencing_token IS DISTINCT FROM p_fencing_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_claim.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_COMMAND_RENEW_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_command_claims SET lease_expires_at = clock_timestamp()
               + make_interval(secs => p_lease_seconds),
           updated_at = clock_timestamp()
     WHERE command_id = p_command_id RETURNING * INTO v_claim;
    RETURN jsonb_build_object(
        'outcome', 'renewed', 'command_id', p_command_id,
        'lease_expires_at', v_claim.lease_expires_at);
END;
$$;

CREATE FUNCTION finish_agent_command_claim(
    p_command_id UUID, p_fencing_token UUID, p_outcome TEXT,
    p_error_class TEXT DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_claim agent_command_claims%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_claim FROM agent_command_claims
     WHERE command_id = p_command_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF v_claim.status <> 'claimed'
       OR v_claim.fencing_token IS DISTINCT FROM p_fencing_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_claim.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF p_outcome NOT IN ('completed', 'failed')
       OR (p_outcome = 'completed' AND p_error_class IS NOT NULL)
       OR (p_error_class IS NOT NULL
           AND length(p_error_class) NOT BETWEEN 1 AND 200) THEN
        RAISE EXCEPTION 'AGENT_COMMAND_FINISH_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_command_claims SET status = p_outcome, outcome = p_outcome,
           error_class = p_error_class, finished_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE command_id = p_command_id;
    RETURN jsonb_build_object(
        'outcome', p_outcome, 'command_id', p_command_id,
        'run_id', v_claim.run_id);
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_command_run_envelope(agent_session_commands),
    _finish_exhausted_agent_command(agent_session_commands, agent_command_claims),
    _reject_agent_command(
        agent_session_commands, agent_runtime_sessions, TEXT, TEXT),
    _ensure_agent_command_run(
        agent_session_commands, agent_runtime_sessions,
        agent_command_claims, JSONB, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION
    claim_pending_agent_command_and_ensure_run(TEXT, INTEGER, INTEGER),
    get_agent_command_run_claim(UUID, TEXT),
    renew_agent_command_claim(UUID, UUID, INTEGER),
    finish_agent_command_claim(UUID, UUID, TEXT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    claim_pending_agent_command_and_ensure_run(TEXT, INTEGER, INTEGER),
    get_agent_command_run_claim(UUID, TEXT),
    renew_agent_command_claim(UUID, UUID, INTEGER),
    finish_agent_command_claim(UUID, UUID, TEXT, TEXT)
TO everydayai_worker;

RESET ROLE;
