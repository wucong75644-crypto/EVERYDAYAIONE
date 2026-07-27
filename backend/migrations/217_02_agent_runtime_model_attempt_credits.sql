-- 217_02: ModelStep credit idempotency metadata over the existing ledger.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_model_credit_settlements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_step_id UUID NOT NULL UNIQUE
        REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
    reservation_attempt_id UUID NOT NULL UNIQUE
        REFERENCES agent_model_attempts(id) ON DELETE RESTRICT,
    effective_attempt_id UUID UNIQUE
        REFERENCES agent_model_attempts(id) ON DELETE RESTRICT,
    credit_transaction_id UUID UNIQUE
        REFERENCES credit_transactions(id) ON DELETE RESTRICT,
    billing_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    reservation_key TEXT NOT NULL UNIQUE,
    settlement_key TEXT UNIQUE,
    adjustment_key TEXT UNIQUE,
    status TEXT NOT NULL CHECK (status IN (
        'reserved', 'settled', 'released', 'adjustment_pending', 'adjusted'
    )),
    reserved_credits INTEGER NOT NULL CHECK (reserved_credits >= 0),
    settled_credits INTEGER NOT NULL DEFAULT 0 CHECK (settled_credits >= 0),
    adjusted_credits INTEGER NOT NULL DEFAULT 0 CHECK (adjusted_credits >= 0),
    response_hash TEXT CHECK (
        response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'
    ),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    settled_at TIMESTAMPTZ,
    adjusted_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE agent_model_credit_settlements ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_model_credit_settlements_owner_all
    ON agent_model_credit_settlements
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_model_credit_settlements FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _reserve_agent_model_credits(
    p_step agent_model_steps, p_attempt_id UUID, p_reserved_credits INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_session agent_runtime_sessions%ROWTYPE;
    v_user_id UUID;
    v_transaction_id UUID;
    v_balance INTEGER;
BEGIN
    IF p_reserved_credits < 0 THEN
        RAISE EXCEPTION 'AGENT_MODEL_RESERVATION_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions WHERE id = p_step.session_id;
    v_user_id := COALESCE(p_step.user_id, v_session.created_by_user_id);
    IF v_user_id IS NULL THEN
        IF p_reserved_credits <> 0 THEN
            RETURN jsonb_build_object('outcome', 'insufficient_credits');
        END IF;
    ELSIF p_reserved_credits > 0 THEN
        UPDATE users SET credits = credits - p_reserved_credits,
               updated_at = clock_timestamp()
         WHERE id = v_user_id AND credits >= p_reserved_credits
        RETURNING credits INTO v_balance;
        IF v_balance IS NULL THEN
            RETURN jsonb_build_object('outcome', 'insufficient_credits');
        END IF;
        v_transaction_id := gen_random_uuid();
        INSERT INTO credit_transactions(
            id, task_id, user_id, amount, type, status, reason, org_id
        ) VALUES (
            v_transaction_id, p_step.id, v_user_id, p_reserved_credits,
            'lock', 'pending', 'Agent Runtime model reservation', p_step.org_id
        );
    END IF;
    INSERT INTO agent_model_credit_settlements(
        model_step_id, reservation_attempt_id, credit_transaction_id,
        billing_user_id, org_id, reservation_key, status, reserved_credits
    ) VALUES (
        p_step.id, p_attempt_id, v_transaction_id, v_user_id, p_step.org_id,
        'reserve:' || p_step.id::TEXT, 'reserved', p_reserved_credits
    );
    RETURN jsonb_build_object(
        'outcome', 'reserved', 'credit_transaction_id', v_transaction_id
    );
END;
$$;

CREATE FUNCTION _settle_agent_model_credits(
    p_step agent_model_steps, p_attempt_id UUID, p_response_hash TEXT,
    p_actual_credits INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_settlement agent_model_credit_settlements%ROWTYPE;
    v_refund INTEGER;
    v_balance INTEGER;
BEGIN
    SELECT * INTO v_settlement FROM agent_model_credit_settlements
     WHERE model_step_id = p_step.id FOR UPDATE;
    IF NOT FOUND OR p_actual_credits < 0
       OR p_actual_credits > v_settlement.reserved_credits THEN
        RAISE EXCEPTION 'AGENT_MODEL_SETTLEMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF v_settlement.status = 'settled' THEN
        IF v_settlement.effective_attempt_id IS DISTINCT FROM p_attempt_id
           OR v_settlement.response_hash IS DISTINCT FROM p_response_hash
           OR v_settlement.settled_credits <> p_actual_credits THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object('outcome', 'already_settled');
    END IF;
    IF v_settlement.status <> 'reserved' THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    v_refund := v_settlement.reserved_credits - p_actual_credits;
    IF v_settlement.credit_transaction_id IS NOT NULL THEN
        UPDATE credit_transactions SET status = CASE
                   WHEN p_actual_credits = 0 THEN 'refunded' ELSE 'confirmed' END,
               confirmed_at = clock_timestamp()
         WHERE id = v_settlement.credit_transaction_id AND status = 'pending';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'AGENT_MODEL_CREDIT_TRANSACTION_CONFLICT'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    IF v_refund > 0 AND v_settlement.billing_user_id IS NOT NULL THEN
        UPDATE users SET credits = credits + v_refund,
               updated_at = clock_timestamp()
         WHERE id = v_settlement.billing_user_id RETURNING credits INTO v_balance;
        INSERT INTO credits_history(
            user_id, change_type, change_amount, balance_after, description, org_id
        ) VALUES (
            v_settlement.billing_user_id, 'partial_refund'::credits_change_type,
            v_refund, v_balance, 'Agent Runtime model reservation settlement',
            v_settlement.org_id
        );
    END IF;
    UPDATE agent_model_credit_settlements SET
           effective_attempt_id = p_attempt_id,
           settlement_key = p_step.id::TEXT || ':' || p_attempt_id::TEXT,
           status = 'settled', settled_credits = p_actual_credits,
           response_hash = p_response_hash, state_version = state_version + 1,
           settled_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = v_settlement.id;
    RETURN jsonb_build_object('outcome', 'settled');
END;
$$;

CREATE FUNCTION _release_agent_model_credits(p_step_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_settlement agent_model_credit_settlements%ROWTYPE;
    v_refund JSONB;
BEGIN
    SELECT * INTO v_settlement FROM agent_model_credit_settlements
     WHERE model_step_id = p_step_id FOR UPDATE;
    IF NOT FOUND OR v_settlement.status = 'released' THEN
        RETURN jsonb_build_object('outcome', 'already_released');
    END IF;
    IF v_settlement.status <> 'reserved' THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_settlement.credit_transaction_id IS NOT NULL THEN
        SELECT atomic_refund_credits(v_settlement.credit_transaction_id)
          INTO v_refund;
        IF COALESCE((v_refund->>'refunded')::BOOLEAN, FALSE) IS NOT TRUE THEN
            RAISE EXCEPTION 'AGENT_MODEL_CREDIT_RELEASE_CONFLICT'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    UPDATE agent_model_credit_settlements SET status = 'released',
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = v_settlement.id;
    RETURN jsonb_build_object('outcome', 'released');
END;
$$;

CREATE FUNCTION _cancel_agent_model_work(p_run_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_step agent_model_steps%ROWTYPE;
    v_attempt agent_model_attempts%ROWTYPE;
BEGIN
    SELECT * INTO v_step FROM agent_model_steps
     WHERE run_id = p_run_id AND status IN ('pending', 'running')
     ORDER BY step_number DESC LIMIT 1 FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT * INTO v_attempt FROM agent_model_attempts
     WHERE model_step_id = v_step.id
       AND status IN ('prepared', 'dispatching', 'unknown')
     FOR UPDATE;
    IF FOUND THEN
        PERFORM _release_agent_model_credits(v_step.id);
        UPDATE agent_model_attempts SET status = 'cancelled',
               retry_disposition = 'forbidden',
               state_version = state_version + 1,
               completed_at = clock_timestamp(), updated_at = clock_timestamp()
         WHERE id = v_attempt.id;
    END IF;
    UPDATE agent_model_steps SET status = 'cancelled',
           stop_reason = 'cancelled', terminal_reason = 'run_cancelled',
           state_version = state_version + 1,
           completed_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = v_step.id;
END;
$$;

REVOKE ALL ON TABLE agent_model_credit_settlements
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION
    _reserve_agent_model_credits(agent_model_steps, UUID, INTEGER),
    _settle_agent_model_credits(agent_model_steps, UUID, TEXT, INTEGER),
    _release_agent_model_credits(UUID),
    _cancel_agent_model_work(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
