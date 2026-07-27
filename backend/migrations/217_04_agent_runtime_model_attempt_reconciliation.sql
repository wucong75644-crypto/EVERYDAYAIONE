-- 217_04: ModelAttempt reconciliation, readback and late receipts.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_model_attempt(p_attempt_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_model_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'attempt', to_jsonb(v_attempt)
    );
END;
$$;

CREATE FUNCTION claim_model_attempt_reconciliation(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_worker_id TEXT,
    p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_token UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.state_version <> p_expected_attempt_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_attempt.status = 'dispatching'
       AND v_attempt.lease_expires_at > clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'busy');
    END IF;
    IF v_attempt.status NOT IN ('dispatching', 'unknown') THEN
        RETURN jsonb_build_object('outcome', 'not_reconcilable');
    END IF;
    IF NULLIF(BTRIM(p_worker_id), '') IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'AGENT_MODEL_RECONCILE_INVALID' USING ERRCODE = '22023';
    END IF;
    v_token := gen_random_uuid();
    UPDATE agent_model_attempts SET status = 'unknown',
           retry_disposition = CASE
               WHEN retry_disposition = 'reconcile_only'
               THEN 'reconcile_only' ELSE 'forbidden' END,
           ambiguity_evidence = ambiguity_evidence || jsonb_build_object(
               'reconciliation_claimed_at', clock_timestamp()
           ),
           worker_id = BTRIM(p_worker_id), execution_token = v_token,
           lease_expires_at = clock_timestamp()
               + make_interval(secs => p_lease_seconds),
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'claimed', 'attempt_id', v_attempt.id,
        'execution_token', v_token, 'lease_expires_at', v_attempt.lease_expires_at,
        'state_version', v_attempt.state_version
    );
END;
$$;

CREATE FUNCTION renew_model_attempt_reconciliation(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_reconciliation_token UUID, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_model_attempts%ROWTYPE; v_run agent_runs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_reconciliation_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp()
       OR v_attempt.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.status <> 'unknown' THEN
        RETURN jsonb_build_object('outcome', 'not_reconcilable');
    END IF;
    UPDATE agent_model_attempts SET lease_expires_at = clock_timestamp()
               + make_interval(secs => p_lease_seconds),
           updated_at = clock_timestamp()
     WHERE id = p_attempt_id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'renewed', 'attempt_id', v_attempt.id,
        'lease_expires_at', v_attempt.lease_expires_at,
        'state_version', v_attempt.state_version
    );
END;
$$;

CREATE FUNCTION resolve_model_attempt(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_reconciliation_token UUID, p_expected_attempt_version BIGINT,
    p_expected_step_version BIGINT, p_resolution TEXT, p_request_hash TEXT,
    p_response_receipt JSONB DEFAULT NULL, p_response_hash TEXT DEFAULT NULL,
    p_stop_reason TEXT DEFAULT NULL, p_provider_stop_reason TEXT DEFAULT NULL,
    p_usage JSONB DEFAULT '{}'::JSONB, p_actual_credits INTEGER DEFAULT 0,
    p_error_code TEXT DEFAULT NULL, p_ambiguity_evidence JSONB DEFAULT '{}'::JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_model_attempts%ROWTYPE; v_run agent_runs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_reconciliation_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp()
       OR v_attempt.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.status <> 'unknown'
       OR v_attempt.state_version <> p_expected_attempt_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF p_resolution = 'still_unknown' THEN
        UPDATE agent_model_attempts SET
               ambiguity_evidence = ambiguity_evidence || p_ambiguity_evidence,
               retry_disposition = 'forbidden',
               state_version = state_version + 1, updated_at = clock_timestamp()
         WHERE id = p_attempt_id RETURNING * INTO v_attempt;
        RETURN jsonb_build_object(
            'outcome', 'still_unknown', 'attempt_id', v_attempt.id,
            'state_version', v_attempt.state_version
        );
    ELSIF p_resolution = 'completed' THEN
        RETURN _complete_model_attempt_without_actions(
            p_attempt_id, p_run_execution_token, p_reconciliation_token,
            p_expected_attempt_version, p_expected_step_version,
            p_request_hash, p_response_receipt, p_response_hash,
            p_stop_reason, p_provider_stop_reason, p_usage, p_actual_credits
        );
    ELSIF p_resolution = 'failed' THEN
        RETURN _fail_model_attempt_and_step(
            p_attempt_id, p_run_execution_token, p_reconciliation_token,
            p_expected_attempt_version, p_expected_step_version,
            p_request_hash, p_error_code, 'forbidden'
        );
    END IF;
    RAISE EXCEPTION 'AGENT_MODEL_RESOLUTION_INVALID' USING ERRCODE = '22023';
END;
$$;

CREATE FUNCTION _adjust_model_attempt_credits(
    p_attempt_id UUID, p_response_hash TEXT, p_actual_credits INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_settlement agent_model_credit_settlements%ROWTYPE;
    v_balance INTEGER;
    v_key TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    SELECT * INTO v_settlement FROM agent_model_credit_settlements
     WHERE model_step_id = v_attempt.model_step_id FOR UPDATE;
    IF v_run.status <> 'cancelled'
       OR v_attempt.status NOT IN ('cancelled', 'unknown')
       OR v_attempt.late_outcome IS DISTINCT FROM 'completed'
       OR v_attempt.response_hash IS DISTINCT FROM p_response_hash THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    v_key := 'adjustment:' || p_attempt_id::TEXT || ':' || p_response_hash;
    IF v_settlement.status = 'adjusted' THEN
        IF v_settlement.adjustment_key IS DISTINCT FROM v_key
           OR v_settlement.adjusted_credits <> p_actual_credits THEN
            RETURN jsonb_build_object('outcome', 'receipt_conflict');
        END IF;
        RETURN jsonb_build_object('outcome', 'already_adjusted');
    END IF;
    IF v_settlement.status NOT IN ('released', 'adjustment_pending')
       OR p_actual_credits < 0 THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF p_actual_credits > 0 AND v_settlement.billing_user_id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'insufficient_credits');
    END IF;
    IF p_actual_credits > 0 THEN
        UPDATE users SET credits = credits - p_actual_credits,
               updated_at = clock_timestamp()
         WHERE id = v_settlement.billing_user_id
           AND credits >= p_actual_credits RETURNING credits INTO v_balance;
        IF v_balance IS NULL THEN
            UPDATE agent_model_credit_settlements SET
                   adjustment_key = v_key, status = 'adjustment_pending',
                   adjusted_credits = p_actual_credits,
                   response_hash = p_response_hash,
                   state_version = state_version + 1,
                   updated_at = clock_timestamp() WHERE id = v_settlement.id;
            RETURN jsonb_build_object('outcome', 'insufficient_credits');
        END IF;
        INSERT INTO credits_history(
            user_id, change_type, change_amount, balance_after, description, org_id
        ) VALUES (
            v_settlement.billing_user_id, 'conversation_cost'::credits_change_type,
            -p_actual_credits, v_balance, 'Agent Runtime late model adjustment',
            v_settlement.org_id
        );
    END IF;
    UPDATE agent_model_credit_settlements SET
           effective_attempt_id = p_attempt_id, adjustment_key = v_key,
           status = 'adjusted', adjusted_credits = p_actual_credits,
           response_hash = p_response_hash, state_version = state_version + 1,
           adjusted_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = v_settlement.id;
    RETURN jsonb_build_object('outcome', 'adjusted');
END;
$$;

CREATE FUNCTION record_late_model_receipt(
    p_attempt_id UUID, p_provider_request_id TEXT, p_response_receipt JSONB,
    p_response_hash TEXT, p_usage JSONB, p_late_outcome TEXT,
    p_ambiguity_evidence JSONB, p_actual_credits INTEGER DEFAULT 0
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_adjustment JSONB := jsonb_build_object('outcome', 'not_required');
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_attempt.run_id FOR UPDATE;
    PERFORM 1 FROM agent_model_steps WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF v_run.status <> 'cancelled'
       OR v_attempt.status NOT IN ('cancelled', 'unknown') THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_attempt.late_outcome IS NOT NULL THEN
        IF v_attempt.provider_request_id IS DISTINCT FROM p_provider_request_id
           OR v_attempt.response_hash IS DISTINCT FROM p_response_hash THEN
            RETURN jsonb_build_object('outcome', 'receipt_conflict');
        END IF;
        IF p_late_outcome = 'completed' THEN
            v_adjustment := _adjust_model_attempt_credits(
                p_attempt_id, p_response_hash, p_actual_credits
            );
            IF v_adjustment->>'outcome' IN (
                'receipt_conflict', 'terminal_conflict'
            ) THEN RETURN v_adjustment; END IF;
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_recorded',
            'settlement_outcome', v_adjustment->'outcome'
        );
    END IF;
    IF p_late_outcome NOT IN ('completed', 'failed')
       OR COALESCE(p_response_hash !~ '^[0-9a-f]{64}$', TRUE)
       OR (v_attempt.provider_request_id IS NOT NULL
           AND v_attempt.provider_request_id IS DISTINCT FROM p_provider_request_id)
       OR jsonb_typeof(p_response_receipt) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_usage) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_ambiguity_evidence) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AGENT_MODEL_LATE_RECEIPT_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_model_attempts SET
           provider_request_id = p_provider_request_id,
           response_receipt = p_response_receipt, response_hash = p_response_hash,
           usage = p_usage, ambiguity_evidence = ambiguity_evidence || p_ambiguity_evidence,
           late_outcome = p_late_outcome, late_receipt_recorded_at = clock_timestamp(),
           retry_disposition = 'forbidden', state_version = state_version + 1,
           updated_at = clock_timestamp() WHERE id = p_attempt_id;
    IF p_late_outcome = 'completed' THEN
        v_adjustment := _adjust_model_attempt_credits(
            p_attempt_id, p_response_hash, p_actual_credits
        );
        IF v_adjustment->>'outcome' IN (
            'receipt_conflict', 'terminal_conflict'
        ) THEN
            RAISE EXCEPTION 'AGENT_MODEL_ADJUSTMENT_CONFLICT'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    RETURN jsonb_build_object(
        'outcome', CASE WHEN v_adjustment->>'outcome' = 'insufficient_credits'
            THEN 'adjustment_pending' ELSE 'recorded' END,
        'attempt_id', p_attempt_id,
        'settlement_outcome', v_adjustment->'outcome'
    );
END;
$$;

REVOKE ALL ON FUNCTION
    get_model_attempt(UUID),
    claim_model_attempt_reconciliation(UUID, UUID, BIGINT, TEXT, INTEGER),
    renew_model_attempt_reconciliation(UUID, UUID, UUID, INTEGER),
    resolve_model_attempt(
        UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, JSONB, TEXT,
        TEXT, TEXT, JSONB, INTEGER, TEXT, JSONB
    ),
    _adjust_model_attempt_credits(UUID, TEXT, INTEGER),
    record_late_model_receipt(UUID, TEXT, JSONB, TEXT, JSONB, TEXT, JSONB, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    get_model_attempt(UUID),
    claim_model_attempt_reconciliation(UUID, UUID, BIGINT, TEXT, INTEGER),
    renew_model_attempt_reconciliation(UUID, UUID, UUID, INTEGER),
    resolve_model_attempt(
        UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, JSONB, TEXT,
        TEXT, TEXT, JSONB, INTEGER, TEXT, JSONB
    ),
    record_late_model_receipt(UUID, TEXT, JSONB, TEXT, JSONB, TEXT, JSONB, INTEGER)
TO everydayai_worker;

RESET ROLE;
