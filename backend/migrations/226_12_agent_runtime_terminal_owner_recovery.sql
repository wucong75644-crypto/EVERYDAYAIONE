-- 226_12: additive owner-finalize and durable sync recovery contracts.
-- Existing 226 lanes remain unchanged; these names are the only new write paths.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION finalize_agent_action_provider_v2(
    p_attempt_id UUID, p_execution_token UUID, p_reconciliation_token UUID,
    p_expected_state_version INTEGER, p_request_hash TEXT, p_terminal_state TEXT,
    p_provider_receipt JSONB, p_result JSONB, p_cost_kind TEXT,
    p_reserved_amount BIGINT, p_actual_amount BIGINT, p_currency TEXT,
    p_reason_code TEXT, p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_action_attempts%ROWTYPE; act agent_actions%ROWTYPE;
        cost_result JSONB; terminal_result JSONB; effective_token UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_terminal_state NOT IN ('completed','failed','cancelled') THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_STATE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_FINALIZE_ATTEMPT_NOT_FOUND' USING ERRCODE='22023'; END IF;
    SELECT * INTO act FROM agent_actions WHERE id=a.action_id FOR UPDATE;
    effective_token := CASE WHEN a.status IN ('accepted','unknown')
        THEN p_reconciliation_token ELSE p_execution_token END;
    IF effective_token IS NULL OR a.state_version IS DISTINCT FROM p_expected_state_version
       OR (a.status IN ('accepted','unknown') AND a.reconciliation_token IS DISTINCT FROM effective_token)
       OR (a.status NOT IN ('accepted','unknown') AND a.execution_token IS DISTINCT FROM effective_token)
       OR a.request_hash IS DISTINCT FROM p_request_hash THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_FENCED' USING ERRCODE='42501';
    END IF;
    IF a.status NOT IN ('dispatching','accepted','unknown')
       OR act.status NOT IN ('running','accepted','unknown') THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
    END IF;
    -- Expiry is a dispatch-gate rule. An already-created immutable intent may reconcile.
    IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents i
        JOIN agent_policy_receipts r ON r.id=i.policy_receipt_id
        WHERE i.attempt_id=a.id AND i.action_id=a.action_id
          AND i.request_hash=p_request_hash AND i.execution_token=a.execution_token
          AND r.action_id=a.action_id AND r.decision='allow') THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_DISPATCH_CONTRACT_MISSING' USING ERRCODE='42501';
    END IF;
    IF jsonb_typeof(COALESCE(p_provider_receipt,'{}')) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_PROVIDER_RECEIPT_INVALID' USING ERRCODE='22023';
    END IF;
    UPDATE agent_action_attempts SET external_receipt=COALESCE(p_provider_receipt,'{}'),
        last_provider_status=p_terminal_state, updated_at=clock_timestamp() WHERE id=a.id;
    IF p_cost_kind IS NOT NULL THEN
        SELECT record_agent_action_cost_strict(a.action_id,a.id,p_cost_kind,
            p_reserved_amount,p_actual_amount,p_currency,p_reason_code,p_provider_receipt_hash)
          INTO cost_result;
    END IF;
    IF p_terminal_state IN ('completed','failed') THEN
        SELECT _finish_agent_action(a.id,effective_token,a.state_version,p_request_hash,
            p_terminal_state,p_result) INTO terminal_result;
        IF terminal_result->>'outcome' NOT IN (p_terminal_state,'already_'||p_terminal_state) THEN
            RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
        END IF;
    ELSE
        UPDATE agent_action_attempts SET status='cancelled',ended_at=clock_timestamp(),
            reconciliation_token=NULL,reconciliation_lease_expires_at=NULL,
            state_version=state_version+1,updated_at=clock_timestamp() WHERE id=a.id;
        UPDATE agent_actions SET status='cancelled',completed_at=clock_timestamp(),
            state_version=state_version+1,updated_at=clock_timestamp() WHERE id=a.action_id;
        PERFORM append_agent_runtime_event(a.session_id,'action.cancelled',a.run_id,
            act.model_step_id,a.action_id,'action_loop',session_user,
            jsonb_build_object('request_hash',p_request_hash),ARRAY['web_runtime','audit']::TEXT[]);
        terminal_result := jsonb_build_object('outcome','cancelled','action_id',a.action_id);
    END IF;
    RETURN jsonb_build_object('outcome',p_terminal_state,'cost',COALESCE(cost_result,'{}'::JSONB),
        'terminal',terminal_result);
END; $$;

CREATE FUNCTION read_agent_sync_phase_facts(
    p_action_id UUID, p_attempt_id UUID, p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE result JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT COALESCE(jsonb_object_agg(phase, jsonb_build_object(
        'checkpoint',checkpoint,'provider_receipt',provider_receipt)), '{}'::JSONB)
      INTO result FROM agent_action_sync_phase_facts
     WHERE action_id=p_action_id AND attempt_id=p_attempt_id AND request_hash=p_request_hash;
    RETURN jsonb_build_object('outcome','readback','facts',COALESCE(result,'{}'::JSONB));
END; $$;

CREATE FUNCTION record_agent_sync_phase_v2(
    p_action_id UUID, p_attempt_id UUID, p_reconciliation_token UUID,
    p_request_hash TEXT, p_phase TEXT, p_checkpoint JSONB, p_provider_receipt JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_action_attempts%ROWTYPE; old agent_action_sync_phase_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id AND action_id=p_action_id FOR UPDATE;
    IF NOT FOUND OR a.request_hash IS DISTINCT FROM p_request_hash
       OR (a.status IN ('accepted','unknown') AND a.reconciliation_token IS DISTINCT FROM p_reconciliation_token)
       OR (a.status NOT IN ('accepted','unknown') AND a.execution_token IS DISTINCT FROM p_reconciliation_token)
       OR a.status NOT IN ('accepted','unknown','dispatching') THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents i
       JOIN agent_policy_receipts r ON r.id=i.policy_receipt_id
       WHERE i.action_id=a.action_id AND i.attempt_id=a.id
         AND i.request_hash=p_request_hash AND i.execution_token=a.execution_token
         AND r.action_id=a.action_id AND r.decision='allow') THEN
        RETURN jsonb_build_object('outcome','dispatch_contract_missing');
    END IF;
    SELECT * INTO old FROM agent_action_sync_phase_facts
      WHERE action_id=p_action_id AND attempt_id=p_attempt_id AND phase=p_phase FOR UPDATE;
    IF FOUND AND (old.checkpoint IS DISTINCT FROM COALESCE(p_checkpoint,'{}')
       OR old.provider_receipt IS DISTINCT FROM COALESCE(p_provider_receipt,'{}')) THEN
        RETURN jsonb_build_object('outcome','idempotency_conflict');
    END IF;
    INSERT INTO agent_action_sync_phase_facts(action_id,attempt_id,request_hash,phase,checkpoint,provider_receipt)
      VALUES (p_action_id,p_attempt_id,p_request_hash,p_phase,COALESCE(p_checkpoint,'{}'),COALESCE(p_provider_receipt,'{}'))
      ON CONFLICT (action_id,attempt_id,phase) DO NOTHING;
    RETURN jsonb_build_object('outcome','recorded','phase',p_phase);
END; $$;

CREATE FUNCTION aggregate_agent_child_run_strict(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_request_hash TEXT, p_parent_attempt_id UUID,
    p_reconciliation_token UUID, p_expected_state_version INTEGER,
    p_aggregation_revision INTEGER, p_result JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE c agent_runs%ROWTYPE; pending INTEGER;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id
      AND parent_run_id=p_parent_run_id AND parent_action_id=p_parent_action_id
      AND parent_request_hash=p_parent_request_hash FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_action_attempts a
      WHERE a.action_id=p_parent_action_id AND a.id=p_parent_attempt_id
        AND (a.execution_token=p_reconciliation_token OR a.reconciliation_token=p_reconciliation_token)
        AND a.state_version=p_expected_state_version) THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    SELECT count(*) INTO pending FROM agent_actions WHERE run_id=c.id
      AND status NOT IN ('completed','failed','rejected','cancelled');
    IF pending > 0 OR c.status NOT IN ('completed','failed','cancelled') THEN
        RETURN jsonb_build_object('outcome','child_not_terminal','pending_actions',pending,'status',c.status);
    END IF;
    RETURN complete_agent_child_run(c.id,p_parent_run_id,p_aggregation_revision,p_result);
END; $$;

CREATE FUNCTION cancel_agent_child_run_strict_v2(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_request_hash TEXT, p_parent_attempt_id UUID,
    p_reconciliation_token UUID, p_expected_state_version INTEGER, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE c agent_runs%ROWTYPE; active_unknown INTEGER;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id
      AND parent_run_id=p_parent_run_id AND parent_action_id=p_parent_action_id
      AND parent_request_hash=p_parent_request_hash FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM agent_action_attempts a
      WHERE a.action_id=p_parent_action_id AND a.id=p_parent_attempt_id
        AND (a.execution_token=p_reconciliation_token OR a.reconciliation_token=p_reconciliation_token)
        AND a.state_version=p_expected_state_version) THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    SELECT count(*) INTO active_unknown FROM agent_actions
      WHERE run_id=c.id AND status IN ('accepted','unknown');
    IF active_unknown > 0 THEN
        RETURN jsonb_build_object('outcome','still_reconciling','active_actions',active_unknown);
    END IF;
    RETURN cancel_agent_child_run_strict(p_child_run_id,p_parent_run_id,p_parent_action_id,p_parent_request_hash,p_reason);
END; $$;

-- Tighten the existing public strict entry point for callers that have not
-- migrated to the v2 parent-fencing signature yet: queued/running child runs
-- are never directly aggregated by a parent Action.
CREATE OR REPLACE FUNCTION complete_agent_child_run_strict(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_request_hash TEXT, p_aggregation_revision INTEGER, p_result JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE c agent_runs%ROWTYPE; pending INTEGER;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id AND parent_run_id=p_parent_run_id
        AND parent_action_id=p_parent_action_id AND parent_request_hash=p_parent_request_hash FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
    SELECT count(*) INTO pending FROM agent_actions WHERE run_id=c.id
        AND status NOT IN ('completed','failed','rejected','cancelled');
    IF pending > 0 OR c.status NOT IN ('completed','failed','cancelled') THEN
        RETURN jsonb_build_object('outcome','child_not_terminal','pending_actions',pending,'status',c.status);
    END IF;
    RETURN complete_agent_child_run(c.id,p_parent_run_id,p_aggregation_revision,p_result);
END; $$;

REVOKE ALL ON FUNCTION finalize_agent_action_provider_v2(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT), read_agent_sync_phase_facts(UUID,UUID,TEXT), record_agent_sync_phase_v2(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB), aggregate_agent_child_run_strict(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB), cancel_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finalize_agent_action_provider_v2(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT), read_agent_sync_phase_facts(UUID,UUID,TEXT), record_agent_sync_phase_v2(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB), aggregate_agent_child_run_strict(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB), cancel_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
