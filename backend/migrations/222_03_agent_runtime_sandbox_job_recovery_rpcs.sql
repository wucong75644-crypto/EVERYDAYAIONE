-- 222_03: Response-loss readback and durable Sandbox Job recovery scanners.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_sandbox_job_by_binding(
    p_external_idempotency_key TEXT,
    p_action_id UUID,
    p_attempt_id UUID,
    p_dispatch_intent_id UUID,
    p_request_hash TEXT,
    p_org_id UUID,
    p_user_id UUID,
    p_session_id UUID,
    p_run_id UUID,
    p_executor_type TEXT,
    p_executor_revision INTEGER,
    p_runtime_revision TEXT
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_candidate UUID;
    v_job agent_sandbox_jobs%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_action agent_actions%ROWTYPE;
BEGIN
    PERFORM _assert_agent_sandbox_actor('runtime');
    IF NULLIF(btrim(p_external_idempotency_key), '') IS NULL
       OR p_request_hash !~ '^[0-9a-f]{64}$'
       OR NULLIF(btrim(p_executor_type), '') IS NULL
       OR p_executor_revision < 1
       OR NULLIF(btrim(p_runtime_revision), '') IS NULL THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_READBACK_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT id INTO v_candidate
      FROM agent_sandbox_jobs
     WHERE external_idempotency_key = p_external_idempotency_key;
    IF v_candidate IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;

    v_job := _lock_agent_sandbox_job(v_candidate);
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = v_job.session_id;
    SELECT * INTO v_action FROM agent_actions WHERE id = v_job.action_id;
    IF NOT _agent_sandbox_runtime_scope_ok(v_session, v_action) THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    IF v_job.action_id IS DISTINCT FROM p_action_id
       OR v_job.attempt_id IS DISTINCT FROM p_attempt_id
       OR v_job.dispatch_intent_id IS DISTINCT FROM p_dispatch_intent_id
       OR v_job.request_hash IS DISTINCT FROM p_request_hash
       OR v_job.org_id IS DISTINCT FROM p_org_id
       OR v_job.user_id IS DISTINCT FROM p_user_id
       OR v_job.session_id IS DISTINCT FROM p_session_id
       OR v_job.run_id IS DISTINCT FROM p_run_id
       OR v_job.executor_type IS DISTINCT FROM btrim(p_executor_type)
       OR v_job.executor_revision IS DISTINCT FROM p_executor_revision
       OR v_job.runtime_revision IS DISTINCT FROM btrim(p_runtime_revision)
    THEN
        RETURN jsonb_build_object('outcome', 'idempotency_conflict');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found',
        'job', _agent_sandbox_runtime_job(v_job)
    );
END;
$$;

CREATE FUNCTION claim_next_recoverable_sandbox_job(
    p_worker_id TEXT,
    p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_candidate UUID;
    v_job agent_sandbox_jobs%ROWTYPE;
    v_token UUID;
BEGIN
    PERFORM _assert_agent_sandbox_actor('sandbox_worker');
    IF NULLIF(btrim(p_worker_id), '') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_RECOVERY_CLAIM_INVALID'
            USING ERRCODE = '22023';
    END IF;

    FOR v_candidate IN
        SELECT id
          FROM agent_sandbox_jobs
         WHERE status = 'queued'
            OR (
                status = 'claimed'
                AND lease_expires_at <= clock_timestamp()
                AND starting_at IS NULL
                AND started_at IS NULL
                AND cancel_accepted_at IS NULL
                AND partial_effects = '{"schema_revision":1,"items":[]}'::JSONB
            )
         ORDER BY queued_at, id
         LIMIT 100
    LOOP
        v_job := _lock_agent_sandbox_job(v_candidate);
        IF NOT (
            v_job.status = 'queued'
            OR (
                v_job.status = 'claimed'
                AND v_job.lease_expires_at <= clock_timestamp()
                AND v_job.starting_at IS NULL
                AND v_job.started_at IS NULL
                AND v_job.cancel_accepted_at IS NULL
                AND v_job.partial_effects =
                    '{"schema_revision":1,"items":[]}'::JSONB
            )
        ) THEN
            CONTINUE;
        END IF;
        v_token := gen_random_uuid();
        UPDATE agent_sandbox_jobs
           SET status = 'claimed',
               claim_worker_id = btrim(p_worker_id),
               claim_token = v_token,
               fencing_token = fencing_token + 1,
               lease_expires_at = clock_timestamp()
                   + make_interval(secs => p_lease_seconds),
               claimed_at = clock_timestamp(),
               state_version = state_version + 1,
               updated_at = clock_timestamp()
         WHERE id = v_job.id
         RETURNING * INTO v_job;
        RETURN jsonb_build_object(
            'outcome', 'claimed', 'job', to_jsonb(v_job)
        );
    END LOOP;
    RETURN jsonb_build_object('outcome', 'not_found');
END;
$$;

CREATE FUNCTION claim_next_sandbox_job_reconciliation(
    p_worker_id TEXT,
    p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_candidate UUID;
    v_job agent_sandbox_jobs%ROWTYPE;
    v_token UUID;
    v_reason TEXT;
BEGIN
    PERFORM _assert_agent_sandbox_actor('sandbox_worker');
    IF NULLIF(btrim(p_worker_id), '') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_RECONCILIATION_CLAIM_INVALID'
            USING ERRCODE = '22023';
    END IF;

    FOR v_candidate IN
        SELECT id
          FROM agent_sandbox_jobs
         WHERE (
                status IN ('starting', 'running', 'cancel_requested')
                AND lease_expires_at <= clock_timestamp()
            )
            OR (
                status = 'unknown'
                AND (
                    reconciliation_token IS NULL
                    OR reconciliation_lease_expires_at <= clock_timestamp()
                )
            )
         ORDER BY updated_at, id
         LIMIT 100
    LOOP
        v_job := _lock_agent_sandbox_job(v_candidate);
        IF v_job.status IN ('starting', 'running', 'cancel_requested')
           AND v_job.lease_expires_at <= clock_timestamp() THEN
            v_reason := CASE v_job.status
                WHEN 'cancel_requested' THEN
                    'SANDBOX_CANCEL_TERMINATION_UNPROVEN'
                ELSE 'SANDBOX_EXECUTION_LEASE_EXPIRED'
            END;
            UPDATE agent_sandbox_jobs
               SET status = 'unknown',
                   ambiguity_evidence = jsonb_build_object(
                       'kind', v_reason
                   ),
                   claim_worker_id = NULL,
                   claim_token = NULL,
                   lease_expires_at = NULL,
                   state_version = state_version + 1,
                   updated_at = clock_timestamp()
             WHERE id = v_job.id
             RETURNING * INTO v_job;
        ELSIF NOT (
            v_job.status = 'unknown'
            AND (
                v_job.reconciliation_token IS NULL
                OR v_job.reconciliation_lease_expires_at <= clock_timestamp()
            )
        ) THEN
            CONTINUE;
        END IF;

        v_token := gen_random_uuid();
        UPDATE agent_sandbox_jobs
           SET reconciliation_worker_id = btrim(p_worker_id),
               reconciliation_token = v_token,
               reconciliation_lease_expires_at = clock_timestamp()
                   + make_interval(secs => p_lease_seconds),
               state_version = state_version + 1,
               updated_at = clock_timestamp()
         WHERE id = v_job.id
         RETURNING * INTO v_job;
        RETURN jsonb_build_object(
            'outcome', 'claimed', 'job', to_jsonb(v_job)
        );
    END LOOP;
    RETURN jsonb_build_object('outcome', 'not_found');
END;
$$;

CREATE FUNCTION get_owned_sandbox_job(
    p_job_id UUID,
    p_worker_id TEXT,
    p_claim_token UUID,
    p_fencing_token BIGINT
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_job agent_sandbox_jobs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_sandbox_actor('sandbox_worker');
    IF NULLIF(btrim(p_worker_id), '') IS NULL
       OR p_claim_token IS NULL
       OR p_fencing_token < 1 THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_OWNED_READ_INVALID'
            USING ERRCODE = '22023';
    END IF;
    v_job := _lock_agent_sandbox_job(p_job_id);
    IF v_job.id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_job.claim_worker_id IS DISTINCT FROM btrim(p_worker_id)
       OR v_job.claim_token IS DISTINCT FROM p_claim_token
       OR v_job.fencing_token IS DISTINCT FROM p_fencing_token
       OR v_job.status NOT IN (
           'claimed', 'starting', 'running', 'cancel_requested'
       ) THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    RETURN jsonb_build_object('outcome', 'found', 'job', to_jsonb(v_job));
END;
$$;

CREATE FUNCTION record_reconciled_sandbox_partials(
    p_job_id UUID,
    p_reconciliation_token UUID,
    p_expected_version BIGINT,
    p_partial_effects JSONB
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_job agent_sandbox_jobs%ROWTYPE;
    v_now TIMESTAMPTZ;
    v_empty CONSTANT JSONB :=
        '{"schema_revision":1,"items":[]}'::JSONB;
BEGIN
    PERFORM _assert_agent_sandbox_actor('sandbox_worker');
    v_job := _lock_agent_sandbox_job(p_job_id);
    IF v_job.id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_job.reconciliation_token IS DISTINCT FROM p_reconciliation_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_job.status <> 'unknown'
       OR v_job.reconciliation_lease_expires_at <= clock_timestamp()
       OR NOT _agent_sandbox_manifest_is_valid(p_partial_effects, 'partial')
       OR jsonb_array_length(p_partial_effects->'items') = 0 THEN
        RETURN jsonb_build_object('outcome', 'invalid_transition');
    END IF;
    IF v_job.partial_effects = p_partial_effects THEN
        RETURN jsonb_build_object(
            'outcome', 'already_partials_recorded', 'job', to_jsonb(v_job)
        );
    END IF;
    IF v_job.state_version <> p_expected_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_job.partial_effects <> v_empty THEN
        RETURN jsonb_build_object('outcome', 'partial_effects_conflict');
    END IF;
    v_now := clock_timestamp();
    UPDATE agent_sandbox_jobs
       SET partial_effects = p_partial_effects,
           partial_effects_recorded_at = v_now,
           cleanup_status = 'pending',
           cleanup_deadline_at = v_now + interval '24 hours',
           state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = v_job.id
     RETURNING * INTO v_job;
    RETURN jsonb_build_object(
        'outcome', 'partials_recorded', 'job', to_jsonb(v_job)
    );
END;
$$;

REVOKE ALL ON FUNCTION
    get_sandbox_job_by_binding(
        TEXT,UUID,UUID,UUID,TEXT,UUID,UUID,UUID,UUID,TEXT,INTEGER,TEXT
    ),
    claim_next_recoverable_sandbox_job(TEXT,INTEGER),
    claim_next_sandbox_job_reconciliation(TEXT,INTEGER),
    get_owned_sandbox_job(UUID,TEXT,UUID,BIGINT),
    record_reconciled_sandbox_partials(UUID,UUID,BIGINT,JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sandbox_worker, everydayai_sync, everydayai;

GRANT EXECUTE ON FUNCTION get_sandbox_job_by_binding(
    TEXT,UUID,UUID,UUID,TEXT,UUID,UUID,UUID,UUID,TEXT,INTEGER,TEXT
) TO everydayai_runtime;

GRANT EXECUTE ON FUNCTION
    claim_next_recoverable_sandbox_job(TEXT,INTEGER),
    claim_next_sandbox_job_reconciliation(TEXT,INTEGER),
    get_owned_sandbox_job(UUID,TEXT,UUID,BIGINT),
    record_reconciled_sandbox_partials(UUID,UUID,BIGINT,JSONB)
TO everydayai_sandbox_worker;

REVOKE EXECUTE ON FUNCTION get_sandbox_job(UUID)
FROM everydayai_sandbox_worker;

RESET ROLE;
