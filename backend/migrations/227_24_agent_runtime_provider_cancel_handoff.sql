-- 227.24: hand cancelled parent Runs to fenced provider cancellation.
SET LOCAL ROLE everydayai_owner;

ALTER TABLE agent_action_attempts
    ADD COLUMN reconciliation_operation TEXT
        CHECK (reconciliation_operation IS NULL OR reconciliation_operation IN ('reconcile','cancel')),
    ADD COLUMN reconciliation_parent_run_state_version BIGINT
        CHECK (reconciliation_parent_run_state_version IS NULL
            OR reconciliation_parent_run_state_version >= 0);

CREATE FUNCTION _finalize_agent_action_cancelled_run_v1(
    p_attempt_id UUID, p_reconciliation_token UUID,
    p_expected_state_version INTEGER, p_request_hash TEXT,
    p_provider_receipt JSONB, p_result JSONB, p_cost_kind TEXT,
    p_reserved_amount BIGINT, p_actual_amount BIGINT, p_currency TEXT,
    p_reason_code TEXT, p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    a agent_action_attempts%ROWTYPE; act agent_actions%ROWTYPE;
    run agent_runs%ROWTYPE; fact agent_runtime_provider_submission_facts%ROWTYPE;
    fence agent_runtime_owner_fences%ROWTYPE; cost_result JSONB; event JSONB;
    kill_context JSONB; submission_id UUID; receipt_fact_version BIGINT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF jsonb_typeof(COALESCE(p_provider_receipt, '{}')) IS DISTINCT FROM 'object'
       OR p_provider_receipt #>> '{evidence,cancel_confirmed}' IS DISTINCT FROM 'true'
       OR COALESCE(p_provider_receipt #>> '{evidence,submission_id}', '')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_provider_receipt #>> '{evidence,state_version}', '') !~ '^[0-9]+$'
       OR p_provider_receipt_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_CANCEL_CONFIRMATION_INVALID' USING ERRCODE='22023';
    END IF;
    submission_id := (p_provider_receipt #>> '{evidence,submission_id}')::UUID;
    receipt_fact_version := (p_provider_receipt #>> '{evidence,state_version}')::BIGINT;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_FINALIZE_ATTEMPT_NOT_FOUND' USING ERRCODE='22023'; END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id=a.session_id FOR UPDATE;
    SELECT * INTO run FROM agent_runs WHERE id=a.run_id FOR UPDATE;
    SELECT * INTO act FROM agent_actions WHERE id=a.action_id FOR UPDATE;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF a.status='cancelled' AND act.status='cancelled' THEN
        RETURN jsonb_build_object('outcome','already_cancelled','action_id',a.action_id);
    END IF;
    IF run.status IS DISTINCT FROM 'cancelled' OR a.status NOT IN ('accepted','unknown')
       OR act.status NOT IN ('accepted','unknown')
       OR a.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR a.reconciliation_lease_expires_at <= clock_timestamp()
       OR a.state_version IS DISTINCT FROM p_expected_state_version
       OR a.request_hash IS DISTINCT FROM p_request_hash THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_FENCED' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents i
        JOIN agent_policy_receipts r ON r.id=i.policy_receipt_id
        WHERE i.attempt_id=a.id AND i.action_id=a.action_id
          AND i.request_hash=p_request_hash AND i.execution_token=a.execution_token
          AND r.action_id=a.action_id AND r.decision='allow') THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_DISPATCH_CONTRACT_MISSING' USING ERRCODE='42501';
    END IF;
    kill_context := _agent_runtime_kill_epoch_context(
        a.id, a.execution_token, a.request_hash, a.state_version, 'cleanup');
    IF kill_context->>'outcome' IS DISTINCT FROM 'allowed' THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_KILL_FENCED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO fence FROM agent_runtime_owner_fences
     WHERE owner_kind='attempt' AND owner_id=a.id
       AND execution_token=a.execution_token FOR UPDATE;
    SELECT * INTO fact FROM agent_runtime_provider_submission_facts
     WHERE id=submission_id FOR UPDATE;
    IF fact.id IS NULL OR fact.attempt_id IS DISTINCT FROM a.id
       OR fact.action_id IS DISTINCT FROM a.action_id OR fact.run_id IS DISTINCT FROM a.run_id
       OR fact.execution_token IS DISTINCT FROM a.execution_token
       OR fact.request_hash IS DISTINCT FROM a.request_hash
       OR fact.state IS DISTINCT FROM 'cancelled' OR fact.cancel_requested_at IS NULL
       OR fact.cancel_confirmed_at IS NULL OR fact.state_version IS DISTINCT FROM receipt_fact_version
       OR fact.provider_revision IS DISTINCT FROM fence.provider_revision THEN
        RAISE EXCEPTION 'AGENT_CANCEL_CONFIRMATION_FACT_MISMATCH' USING ERRCODE='42501';
    END IF;
    UPDATE agent_action_attempts SET status='cancelled',
        external_receipt=p_provider_receipt, last_provider_status='cancelled',
        cancel_requested_at=COALESCE(cancel_requested_at,fact.cancel_requested_at),
        cancel_confirmed_at=fact.cancel_confirmed_at, ended_at=clock_timestamp(),
        reconciliation_token=NULL,reconciliation_lease_expires_at=NULL,
        next_reconcile_at=NULL,state_version=state_version+1,updated_at=clock_timestamp()
     WHERE id=a.id;
    UPDATE agent_actions SET status='cancelled',terminal_reason='provider_cancel_confirmed',
        completed_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp()
     WHERE id=act.id;
    IF p_cost_kind IS NOT NULL THEN
        SELECT record_agent_action_cost_strict(act.id,a.id,p_cost_kind,p_reserved_amount,
            p_actual_amount,p_currency,p_reason_code,p_provider_receipt_hash) INTO cost_result;
    END IF;
    event := append_agent_runtime_event(a.session_id,'action.cancelled',a.run_id,
        act.model_step_id,act.id,'reconciler',session_user,
        jsonb_build_object('action_id',act.id,'request_hash',p_request_hash,
            'provider_cancel_confirmed',true),ARRAY['web_runtime','audit']::TEXT[]);
    RETURN jsonb_build_object('outcome','cancelled','action_id',act.id,
        'run_status',run.status,'blocking_action_count',run.blocking_action_count,
        'cost',COALESCE(cost_result,'{}'::JSONB),'event_sequence',event->'event_sequence');
END;
$$;

CREATE OR REPLACE FUNCTION finalize_agent_action_provider_v2(
    p_attempt_id UUID, p_execution_token UUID, p_reconciliation_token UUID,
    p_expected_state_version INTEGER, p_request_hash TEXT, p_terminal_state TEXT,
    p_provider_receipt JSONB, p_result JSONB, p_cost_kind TEXT,
    p_reserved_amount BIGINT, p_actual_amount BIGINT, p_currency TEXT,
    p_reason_code TEXT, p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; act agent_actions%ROWTYPE;
  cost_result JSONB; terminal_result JSONB; effective_token UUID; run_status TEXT;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  IF p_terminal_state NOT IN ('completed','failed','cancelled') OR p_provider_receipt_hash IS NULL
     OR p_provider_receipt_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_CONTRACT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_FINALIZE_ATTEMPT_NOT_FOUND'; END IF;
  SELECT status INTO run_status FROM agent_runs WHERE id=a.run_id;
  IF run_status='cancelled' THEN
    IF p_terminal_state IS DISTINCT FROM 'cancelled' THEN
      RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
    END IF;
    RETURN _finalize_agent_action_cancelled_run_v1(p_attempt_id,p_reconciliation_token,
      p_expected_state_version,p_request_hash,p_provider_receipt,p_result,p_cost_kind,
      p_reserved_amount,p_actual_amount,p_currency,p_reason_code,p_provider_receipt_hash);
  END IF;
  SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
  SELECT * INTO act FROM agent_actions WHERE id=a.action_id FOR UPDATE;
  IF p_terminal_state='cancelled' AND a.status='cancelled' AND act.status='cancelled' THEN
    RETURN jsonb_build_object('outcome','already_cancelled','action_id',a.action_id);
  END IF;
  effective_token:=CASE WHEN a.status IN ('accepted','unknown') THEN p_reconciliation_token ELSE p_execution_token END;
  IF effective_token IS NULL OR a.state_version IS DISTINCT FROM p_expected_state_version
     OR (a.status IN ('accepted','unknown') AND a.reconciliation_token IS DISTINCT FROM effective_token)
     OR (a.status NOT IN ('accepted','unknown') AND a.execution_token IS DISTINCT FROM effective_token)
     OR a.request_hash IS DISTINCT FROM p_request_hash THEN RAISE EXCEPTION 'AGENT_FINALIZE_FENCED' USING ERRCODE='42501'; END IF;
  IF a.status NOT IN ('dispatching','accepted','unknown') OR act.status NOT IN ('running','accepted','unknown') THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents i JOIN agent_policy_receipts r ON r.id=i.policy_receipt_id
    WHERE i.attempt_id=a.id AND i.action_id=a.action_id AND i.request_hash=p_request_hash
      AND i.execution_token=a.execution_token AND r.action_id=a.action_id AND r.decision='allow') THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_DISPATCH_CONTRACT_MISSING' USING ERRCODE='42501';
  END IF;
  UPDATE agent_action_attempts SET external_receipt=COALESCE(p_provider_receipt,'{}'),last_provider_status=p_terminal_state,updated_at=clock_timestamp() WHERE id=a.id;
  IF p_cost_kind IS NOT NULL THEN
    SELECT record_agent_action_cost_strict(a.action_id,a.id,p_cost_kind,p_reserved_amount,p_actual_amount,p_currency,p_reason_code,p_provider_receipt_hash) INTO cost_result;
  END IF;
  SELECT _finish_agent_action(a.id,effective_token,a.state_version,p_request_hash,p_terminal_state,
    COALESCE(p_result,jsonb_build_object('status',p_terminal_state,'external_receipt',p_provider_receipt))) INTO terminal_result;
  IF terminal_result->>'outcome' NOT IN (p_terminal_state,'already_'||p_terminal_state) THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
  END IF;
  RETURN jsonb_build_object('outcome',p_terminal_state,'cost',COALESCE(cost_result,'{}'),'terminal',terminal_result);
END; $$;

CREATE OR REPLACE FUNCTION claim_agent_action_reconciliation(
    p_attempt_id UUID, p_expected_state_version BIGINT,
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; run agent_runs%ROWTYPE; token UUID; operation TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id=a.session_id FOR UPDATE;
    SELECT * INTO run FROM agent_runs WHERE id=a.run_id FOR UPDATE;
    PERFORM 1 FROM agent_actions WHERE id=a.action_id FOR UPDATE;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF a.status NOT IN ('accepted','unknown') THEN RETURN jsonb_build_object('outcome','not_reconcilable'); END IF;
    IF a.state_version<>p_expected_state_version THEN RETURN jsonb_build_object('outcome','stale_version'); END IF;
    IF a.reconciliation_token IS NOT NULL AND a.reconciliation_lease_expires_at>clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','busy');
    END IF;
    IF NULLIF(btrim(p_worker_id),'') IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'AGENT_ACTION_RECONCILE_INVALID' USING ERRCODE='22023';
    END IF;
    operation:=CASE WHEN run.status='cancelled' THEN 'cancel' ELSE 'reconcile' END;
    token:=gen_random_uuid();
    UPDATE agent_action_attempts SET reconciliation_token=token,
        reconciliation_lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
        reconciliation_operation=operation,
        reconciliation_parent_run_state_version=run.state_version,
        worker_id=btrim(p_worker_id),state_version=state_version+1,updated_at=clock_timestamp()
     WHERE id=p_attempt_id RETURNING * INTO a;
    RETURN jsonb_build_object('outcome','claimed','operation',operation,
        'parent_run_id',run.id,'parent_run_status',run.status,
        'parent_run_state_version',run.state_version,'attempt_id',a.id,
        'execution_token',token,'lease_expires_at',a.reconciliation_lease_expires_at,
        'state_version',a.state_version);
END; $$;

CREATE OR REPLACE FUNCTION claim_next_agent_action_reconciliation(
    p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 120,p_min_age_seconds INTEGER DEFAULT 30
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE candidate RECORD; claim JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_min_age_seconds NOT BETWEEN 0 AND 86400 THEN
        RAISE EXCEPTION 'AGENT_ACTION_RECONCILE_SCAN_INVALID' USING ERRCODE='22023';
    END IF;
    FOR candidate IN
        SELECT attempt.id,attempt.state_version
          FROM agent_action_attempts attempt
          JOIN agent_action_dispatch_intents intent ON intent.attempt_id=attempt.id
         WHERE attempt.status='dispatching'
           AND attempt.lease_expires_at<=clock_timestamp()
         ORDER BY attempt.updated_at,attempt.id LIMIT 100
    LOOP
        PERFORM 1 FROM agent_runtime_sessions WHERE id=(
            SELECT session_id FROM agent_action_attempts WHERE id=candidate.id) FOR UPDATE;
        PERFORM 1 FROM agent_runs WHERE id=(
            SELECT run_id FROM agent_action_attempts WHERE id=candidate.id) FOR UPDATE;
        PERFORM 1 FROM agent_actions WHERE id=(
            SELECT action_id FROM agent_action_attempts WHERE id=candidate.id) FOR UPDATE;
        UPDATE agent_action_attempts SET status='unknown',
            ambiguity_evidence=jsonb_build_object('kind','dispatch_intent_outcome_unproven'),
            retry_disposition='retry_after_reconcile',state_version=state_version+1,
            updated_at=clock_timestamp()
         WHERE id=candidate.id AND status='dispatching';
        UPDATE agent_actions SET status='unknown',
            retry_disposition='retry_after_reconcile',state_version=state_version+1,
            updated_at=clock_timestamp()
         WHERE id=(SELECT action_id FROM agent_action_attempts WHERE id=candidate.id)
           AND status='running';
    END LOOP;
    FOR candidate IN SELECT attempt.id,attempt.state_version FROM agent_action_attempts attempt
      WHERE attempt.status IN ('accepted','unknown')
        AND (attempt.reconciliation_token IS NULL OR attempt.reconciliation_lease_expires_at<=clock_timestamp())
        AND attempt.updated_at<=clock_timestamp()-make_interval(secs=>p_min_age_seconds)
      ORDER BY attempt.updated_at,attempt.id LIMIT 100 LOOP
        claim:=claim_agent_action_reconciliation(candidate.id,candidate.state_version,p_worker_id,p_lease_seconds);
        IF claim->>'outcome'='claimed' THEN
            RETURN claim||jsonb_build_object('snapshot',_agent_action_dispatch_snapshot(
                (SELECT attempt FROM agent_action_attempts attempt WHERE attempt.id=candidate.id)));
        END IF;
    END LOOP;
    RETURN jsonb_build_object('outcome','not_found');
END; $$;

CREATE OR REPLACE FUNCTION get_claimed_agent_action_reconciliation(p_worker_id TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; run agent_runs%ROWTYPE; operation TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE worker_id=btrim(p_worker_id)
      AND status IN ('accepted','unknown') AND reconciliation_token IS NOT NULL
      AND reconciliation_lease_expires_at>clock_timestamp()
      ORDER BY updated_at DESC,id LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    SELECT * INTO run FROM agent_runs WHERE id=a.run_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_ACTION_RECOVERY_RUN_MISSING' USING ERRCODE='55000'; END IF;
    operation:=a.reconciliation_operation;
    IF operation NOT IN ('reconcile','cancel')
       OR a.reconciliation_parent_run_state_version IS DISTINCT FROM run.state_version
       OR (operation='cancel') IS DISTINCT FROM (run.status='cancelled') THEN
        RAISE EXCEPTION 'AGENT_ACTION_RECOVERY_RUN_CHANGED' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object('outcome','found','operation',operation,
      'parent_run_id',run.id,'parent_run_status',run.status,
      'parent_run_state_version',a.reconciliation_parent_run_state_version,'attempt_id',a.id,
      'execution_token',a.reconciliation_token,'lease_expires_at',a.reconciliation_lease_expires_at,
      'state_version',a.state_version,'snapshot',_agent_action_dispatch_snapshot(a));
END; $$;

REVOKE ALL ON FUNCTION _finalize_agent_action_cancelled_run_v1(UUID,UUID,INTEGER,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT),
 finalize_agent_action_provider_v2(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT),
 claim_agent_action_reconciliation(UUID,BIGINT,TEXT,INTEGER),
 claim_next_agent_action_reconciliation(TEXT,INTEGER,INTEGER),
 get_claimed_agent_action_reconciliation(TEXT),
 request_agent_runtime_provider_cancel(UUID,UUID,TEXT,BIGINT,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_agent_runtime_worker,everydayai_agent_model_gateway,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
 finalize_agent_action_provider_v2(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT),
 claim_next_agent_action_reconciliation(TEXT,INTEGER,INTEGER),
 get_claimed_agent_action_reconciliation(TEXT),
 request_agent_runtime_provider_cancel(UUID,UUID,TEXT,BIGINT,TEXT)
TO everydayai_agent_runtime_worker;

RESET ROLE;
