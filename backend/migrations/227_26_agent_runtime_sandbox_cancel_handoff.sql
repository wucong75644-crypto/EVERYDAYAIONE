-- 227.26: fenced Runtime handoff for Sandbox cancellation.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_sandbox_cancel_terminal_guard_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
    IF OLD.cancel_requested_at IS NOT NULL AND (
        NEW.cancel_requested_at IS NULL
        OR NEW.status IN ('succeeded','failed','timed_out')
    ) THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_TERMINAL_FENCED'
            USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER agent_sandbox_cancel_terminal_fence
BEFORE UPDATE ON agent_sandbox_jobs
FOR EACH ROW EXECUTE FUNCTION _agent_sandbox_cancel_terminal_guard_v1();

CREATE FUNCTION request_agent_runtime_sandbox_cancel_v1(
    p_job_id UUID, p_attempt_id UUID, p_reconciliation_token UUID,
    p_expected_action_state_version BIGINT, p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    a agent_action_attempts%ROWTYPE; act agent_actions%ROWTYPE;
    run agent_runs%ROWTYPE; job agent_sandbox_jobs%ROWTYPE;
    intent agent_action_dispatch_intents%ROWTYPE; kill_context JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_request_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_CONTRACT_INVALID'
            USING ERRCODE='22023';
    END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id=a.session_id FOR UPDATE;
    SELECT * INTO run FROM agent_runs WHERE id=a.run_id FOR UPDATE;
    SELECT * INTO act FROM agent_actions WHERE id=a.action_id FOR UPDATE;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    SELECT * INTO intent FROM agent_action_dispatch_intents
     WHERE attempt_id=a.id AND action_id=a.action_id FOR UPDATE;
    SELECT * INTO job FROM agent_sandbox_jobs WHERE id=p_job_id FOR UPDATE;
    IF job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF run.status IS DISTINCT FROM 'cancelled'
       OR act.tool_name IS DISTINCT FROM 'code_execute'
       OR act.status NOT IN ('accepted','unknown')
       OR a.status NOT IN ('accepted','unknown')
       OR a.reconciliation_operation IS DISTINCT FROM 'cancel'
       OR a.reconciliation_parent_run_state_version IS DISTINCT FROM run.state_version
       OR a.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR a.reconciliation_lease_expires_at<=clock_timestamp()
       OR a.state_version IS DISTINCT FROM p_expected_action_state_version
       OR a.request_hash IS DISTINCT FROM p_request_hash THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_ACTION_FENCED'
            USING ERRCODE='42501';
    END IF;
    IF intent.id IS NULL OR intent.execution_token IS DISTINCT FROM a.execution_token
       OR intent.request_hash IS DISTINCT FROM a.request_hash
       OR intent.recovery_mode IS DISTINCT FROM 'reconcile_only'
       OR job.session_id IS DISTINCT FROM a.session_id
       OR job.run_id IS DISTINCT FROM a.run_id
       OR job.action_id IS DISTINCT FROM a.action_id
       OR job.attempt_id IS DISTINCT FROM a.id
       OR job.dispatch_intent_id IS DISTINCT FROM intent.id
       OR job.request_hash IS DISTINCT FROM a.request_hash THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_BINDING_FENCED'
            USING ERRCODE='42501';
    END IF;
    kill_context:=_agent_runtime_kill_epoch_context(
        a.id,a.execution_token,a.request_hash,a.state_version,'cleanup');
    IF kill_context->>'outcome' IS DISTINCT FROM 'allowed' THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_KILL_FENCED'
            USING ERRCODE='42501';
    END IF;
    IF job.status='cancelled' THEN
        RETURN jsonb_build_object('outcome','cancelled',
            'job',_agent_sandbox_runtime_job(job));
    ELSIF job.status IN ('succeeded','failed','timed_out') THEN
        RETURN jsonb_build_object('outcome','terminal_conflict');
    ELSIF job.status='unknown' THEN
        IF job.cancel_requested_at IS NULL THEN
            UPDATE agent_sandbox_jobs SET cancel_requested_at=clock_timestamp(),
                state_version=state_version+1,updated_at=clock_timestamp()
             WHERE id=job.id RETURNING * INTO job;
        END IF;
        RETURN jsonb_build_object('outcome','unknown',
            'job',_agent_sandbox_runtime_job(job));
    ELSIF job.status='cancel_requested' THEN
        RETURN jsonb_build_object('outcome','already_cancel_requested',
            'job',_agent_sandbox_runtime_job(job));
    ELSIF job.status IN ('queued','claimed','starting','running') THEN
        UPDATE agent_sandbox_jobs SET status='cancel_requested',
            cancel_requested_at=clock_timestamp(),state_version=state_version+1,
            updated_at=clock_timestamp() WHERE id=job.id RETURNING * INTO job;
        RETURN jsonb_build_object('outcome','cancel_requested',
            'job',_agent_sandbox_runtime_job(job));
    END IF;
    RETURN jsonb_build_object('outcome','invalid_transition');
END;
$$;

CREATE FUNCTION claim_next_sandbox_cancel_v1(
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE candidate UUID; job agent_sandbox_jobs%ROWTYPE; token UUID;
BEGIN
    PERFORM _assert_agent_sandbox_actor('sandbox_worker');
    IF NULLIF(btrim(p_worker_id),'') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_CLAIM_INVALID'
            USING ERRCODE='22023';
    END IF;
    FOR candidate IN SELECT id FROM agent_sandbox_jobs
      WHERE status='cancel_requested' AND claim_token IS NULL
        AND starting_at IS NULL AND started_at IS NULL
        AND cancel_requested_at IS NOT NULL AND cancel_accepted_at IS NULL
        AND artifact_manifest='{"schema_revision":1,"items":[]}'::jsonb
        AND partial_effects='{"schema_revision":1,"items":[]}'::jsonb
      ORDER BY updated_at,id LIMIT 100 LOOP
        job:=_lock_agent_sandbox_job(candidate);
        IF job.status<>'cancel_requested' OR job.claim_token IS NOT NULL
           OR job.starting_at IS NOT NULL OR job.started_at IS NOT NULL
           OR job.cancel_requested_at IS NULL OR job.cancel_accepted_at IS NOT NULL
           OR job.artifact_manifest<>'{"schema_revision":1,"items":[]}'::jsonb
           OR job.partial_effects<>'{"schema_revision":1,"items":[]}'::jsonb THEN
            CONTINUE;
        END IF;
        token:=gen_random_uuid();
        UPDATE agent_sandbox_jobs SET claim_worker_id=btrim(p_worker_id),
            claim_token=token,fencing_token=fencing_token+1,
            lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
            claimed_at=clock_timestamp(),cancel_accepted_at=clock_timestamp(),
            cancel_confirmed_at=clock_timestamp(),state_version=state_version+1,
            updated_at=clock_timestamp() WHERE id=job.id RETURNING * INTO job;
        RETURN jsonb_build_object('outcome','claimed','job',to_jsonb(job));
    END LOOP;
    RETURN jsonb_build_object('outcome','not_found');
END;
$$;

CREATE FUNCTION finalize_agent_action_sandbox_cancel_v1(
    p_attempt_id UUID, p_reconciliation_token UUID,
    p_expected_state_version BIGINT, p_request_hash TEXT,
    p_sandbox_job_id UUID, p_expected_job_state_version BIGINT,
    p_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    a agent_action_attempts%ROWTYPE; act agent_actions%ROWTYPE;
    run agent_runs%ROWTYPE; job agent_sandbox_jobs%ROWTYPE;
    intent agent_action_dispatch_intents%ROWTYPE; kill_context JSONB; event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_request_hash !~ '^[0-9a-f]{64}$' OR p_receipt_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_FINALIZE_CONTRACT_INVALID'
            USING ERRCODE='22023';
    END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id=a.session_id FOR UPDATE;
    SELECT * INTO run FROM agent_runs WHERE id=a.run_id FOR UPDATE;
    SELECT * INTO act FROM agent_actions WHERE id=a.action_id FOR UPDATE;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    SELECT * INTO intent FROM agent_action_dispatch_intents
     WHERE attempt_id=a.id AND action_id=a.action_id FOR UPDATE;
    SELECT * INTO job FROM agent_sandbox_jobs WHERE id=p_sandbox_job_id FOR UPDATE;
    IF job.id IS NULL OR run.status IS DISTINCT FROM 'cancelled'
       OR act.tool_name IS DISTINCT FROM 'code_execute'
       OR job.session_id IS DISTINCT FROM a.session_id
       OR job.run_id IS DISTINCT FROM a.run_id
       OR job.action_id IS DISTINCT FROM a.action_id
       OR job.attempt_id IS DISTINCT FROM a.id
       OR job.dispatch_intent_id IS DISTINCT FROM intent.id
       OR job.request_hash IS DISTINCT FROM p_request_hash
       OR job.status IS DISTINCT FROM 'cancelled'
       OR job.cancel_requested_at IS NULL OR job.cancel_accepted_at IS NULL
       OR job.cancel_confirmed_at IS NULL OR job.terminal_at IS NULL
       OR job.state_version IS DISTINCT FROM p_expected_job_state_version
       OR job.receipt_hash IS DISTINCT FROM p_receipt_hash
       OR job.cleanup_status NOT IN ('not_required','completed')
       OR (job.cleanup_status='not_required' AND job.cleanup_evidence<>'{}'::jsonb)
       OR (job.cleanup_status='completed'
           AND NOT _agent_sandbox_evidence_is_valid(job.cleanup_evidence))
       OR (jsonb_array_length(job.partial_effects->'items')>0 AND (
           job.cleanup_status<>'completed'
           OR NOT _agent_sandbox_evidence_is_valid(job.cleanup_evidence))) THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_PROOF_INVALID'
            USING ERRCODE='42501';
    END IF;
    IF a.status='cancelled' AND act.status='cancelled' THEN
        IF a.external_receipt->>'sandbox_job_id' IS DISTINCT FROM job.id::text
           OR a.external_receipt->>'receipt_hash' IS DISTINCT FROM job.receipt_hash THEN
            RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_TERMINAL_CONFLICT'
                USING ERRCODE='40001';
        END IF;
        RETURN jsonb_build_object('outcome','already_cancelled','action_id',act.id);
    END IF;
    IF act.status NOT IN ('accepted','unknown') OR a.status NOT IN ('accepted','unknown')
       OR a.reconciliation_operation IS DISTINCT FROM 'cancel'
       OR a.reconciliation_parent_run_state_version IS DISTINCT FROM run.state_version
       OR a.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR a.reconciliation_lease_expires_at<=clock_timestamp()
       OR a.state_version IS DISTINCT FROM p_expected_state_version
       OR a.request_hash IS DISTINCT FROM p_request_hash
       OR intent.execution_token IS DISTINCT FROM a.execution_token
       OR intent.request_hash IS DISTINCT FROM a.request_hash
       OR intent.recovery_mode IS DISTINCT FROM 'reconcile_only' THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_FINALIZE_FENCED' USING ERRCODE='42501';
    END IF;
    kill_context:=_agent_runtime_kill_epoch_context(
        a.id,a.execution_token,a.request_hash,a.state_version,'cleanup');
    IF kill_context->>'outcome' IS DISTINCT FROM 'allowed' THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_FINALIZE_KILL_FENCED' USING ERRCODE='42501';
    END IF;
    UPDATE agent_action_attempts SET status='cancelled',
        external_receipt=jsonb_build_object(
            'sandbox_job_id',job.id,'status','cancelled',
            'state_version',job.state_version,'receipt_hash',job.receipt_hash,
            'cleanup_status',job.cleanup_status,'cancel_confirmed',true),
        last_provider_status='cancelled',
        cancel_requested_at=COALESCE(cancel_requested_at,job.cancel_requested_at),
        cancel_confirmed_at=job.cancel_confirmed_at,ended_at=clock_timestamp(),
        retry_disposition='non_retryable',reconciliation_token=NULL,
        reconciliation_lease_expires_at=NULL,next_reconcile_at=NULL,
        state_version=state_version+1,updated_at=clock_timestamp()
     WHERE id=a.id;
    UPDATE agent_actions SET status='cancelled',retry_disposition='non_retryable',
        terminal_reason='sandbox_cancel_confirmed',completed_at=clock_timestamp(),
        state_version=state_version+1,updated_at=clock_timestamp() WHERE id=act.id;
    event:=append_agent_runtime_event(a.session_id,'action.cancelled',a.run_id,
        act.model_step_id,act.id,'reconciler',session_user,
        jsonb_build_object('action_id',act.id,'request_hash',p_request_hash,
            'sandbox_cancel_confirmed',true),ARRAY['web_runtime','audit']::text[]);
    RETURN jsonb_build_object('outcome','cancelled','action_id',act.id,
        'run_status',run.status,'blocking_action_count',run.blocking_action_count,
        'event_sequence',event->'event_sequence');
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_sandbox_cancel_terminal_guard_v1(),
    request_agent_runtime_sandbox_cancel_v1(UUID,UUID,UUID,BIGINT,TEXT),
    claim_next_sandbox_cancel_v1(TEXT,INTEGER),
    finalize_agent_action_sandbox_cancel_v1(UUID,UUID,BIGINT,TEXT,UUID,BIGINT,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_agent_model_gateway,everydayai_projection_worker,
    everydayai_authorization_worker,everydayai_sandbox_worker;

REVOKE EXECUTE ON FUNCTION request_sandbox_job_cancel(UUID,BIGINT)
FROM everydayai_agent_runtime_worker,everydayai_runtime,everydayai_wecom_runtime,
    everydayai_worker,everydayai_sync,everydayai;

GRANT EXECUTE ON FUNCTION
    request_agent_runtime_sandbox_cancel_v1(UUID,UUID,UUID,BIGINT,TEXT),
    finalize_agent_action_sandbox_cancel_v1(UUID,UUID,BIGINT,TEXT,UUID,BIGINT,TEXT)
TO everydayai_agent_runtime_worker;

GRANT EXECUTE ON FUNCTION claim_next_sandbox_cancel_v1(TEXT,INTEGER)
TO everydayai_sandbox_worker;

RESET ROLE;
