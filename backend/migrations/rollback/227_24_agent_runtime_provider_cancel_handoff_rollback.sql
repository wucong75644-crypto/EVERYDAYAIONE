-- Restore pre-227_24 contracts only when no provider cancellation depends on them.
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM agent_action_attempts attempt
          JOIN agent_runs run ON run.id = attempt.run_id
         WHERE run.status = 'cancelled'
           AND (attempt.status IN ('accepted', 'unknown')
             OR (attempt.reconciliation_token IS NOT NULL
                 AND attempt.reconciliation_lease_expires_at > clock_timestamp()))
    ) OR EXISTS (
        SELECT 1 FROM agent_runtime_provider_submission_facts fact
        JOIN agent_runs run ON run.id=fact.run_id
        WHERE run.status='cancelled'
          AND (fact.state='cancel_requested'
            OR (fact.cancel_requested_at IS NOT NULL AND fact.cancel_confirmed_at IS NULL))
    ) THEN
        RAISE EXCEPTION
            'AGENT_ACTION_CANCEL_HANDOFF_ROLLBACK_PENDING_FACTS'
            USING ERRCODE = '55000';
    END IF;
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
  cost_result JSONB; terminal_result JSONB; effective_token UUID;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  IF p_terminal_state NOT IN ('completed','failed','cancelled') OR p_provider_receipt_hash IS NULL
     OR p_provider_receipt_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_CONTRACT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_FINALIZE_ATTEMPT_NOT_FOUND'; END IF;
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

CREATE OR REPLACE FUNCTION claim_next_agent_action_reconciliation(
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120,
    p_min_age_seconds INTEGER DEFAULT 30
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_candidate RECORD; v_claim JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_min_age_seconds NOT BETWEEN 0 AND 86400 THEN
        RAISE EXCEPTION 'AGENT_ACTION_RECONCILE_SCAN_INVALID'
            USING ERRCODE = '22023';
    END IF;
    FOR v_candidate IN
        SELECT attempt.id, attempt.state_version
          FROM agent_action_attempts attempt
         WHERE attempt.status IN ('accepted', 'unknown')
           AND (attempt.reconciliation_token IS NULL
                OR attempt.reconciliation_lease_expires_at <= clock_timestamp())
           AND attempt.updated_at <= clock_timestamp()
               - make_interval(secs => p_min_age_seconds)
         ORDER BY attempt.updated_at, attempt.id
         LIMIT 100
    LOOP
        v_claim := claim_agent_action_reconciliation(
            v_candidate.id, v_candidate.state_version,
            p_worker_id, p_lease_seconds);
        IF v_claim->>'outcome' = 'claimed' THEN
            RETURN v_claim || jsonb_build_object(
                'snapshot', _agent_action_dispatch_snapshot(
                    (SELECT attempt FROM agent_action_attempts attempt
                      WHERE attempt.id = v_candidate.id)));
        END IF;
    END LOOP;
    RETURN jsonb_build_object('outcome', 'not_found');
END;
$$;

CREATE OR REPLACE FUNCTION get_claimed_agent_action_reconciliation(p_worker_id TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE worker_id = BTRIM(p_worker_id)
       AND status IN ('accepted', 'unknown')
       AND reconciliation_token IS NOT NULL
       AND reconciliation_lease_expires_at > clock_timestamp()
     ORDER BY updated_at DESC, id LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'attempt_id', v_attempt.id,
        'execution_token', v_attempt.reconciliation_token,
        'lease_expires_at', v_attempt.reconciliation_lease_expires_at,
        'state_version', v_attempt.state_version,
        'snapshot', _agent_action_dispatch_snapshot(v_attempt));
END;
$$;

REVOKE ALL ON FUNCTION
    _finalize_agent_action_cancelled_run_v1(UUID, UUID, INTEGER, TEXT, JSONB, JSONB, TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT),
    finalize_agent_action_provider_v2(UUID, UUID, UUID, INTEGER, TEXT, TEXT, JSONB, JSONB, TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT),
    claim_agent_action_reconciliation(UUID, BIGINT, TEXT, INTEGER),
    claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER),
    get_claimed_agent_action_reconciliation(TEXT),
    request_agent_runtime_provider_cancel(UUID, UUID, TEXT, BIGINT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai,
    everydayai_agent_runtime_worker, everydayai_agent_model_gateway,
    everydayai_projection_worker, everydayai_authorization_worker,
    everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
    finalize_agent_action_provider_v2(UUID, UUID, UUID, INTEGER, TEXT, TEXT, JSONB, JSONB, TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT),
    claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER),
    get_claimed_agent_action_reconciliation(TEXT)
TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION
    request_agent_runtime_provider_cancel(UUID, UUID, TEXT, BIGINT, TEXT)
TO everydayai_agent_runtime_worker, everydayai_worker;

DROP FUNCTION _finalize_agent_action_cancelled_run_v1(
    UUID, UUID, INTEGER, TEXT, JSONB, JSONB, TEXT,
    BIGINT, BIGINT, TEXT, TEXT, TEXT);

ALTER TABLE agent_action_attempts
    DROP COLUMN reconciliation_parent_run_state_version,
    DROP COLUMN reconciliation_operation;

RESET ROLE;
