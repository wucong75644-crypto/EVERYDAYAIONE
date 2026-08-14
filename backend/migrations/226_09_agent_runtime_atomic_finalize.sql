-- 226_09: atomic provider terminal + cost + ActionResult finalization.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION finalize_agent_action_provider(
    p_attempt_id UUID, p_execution_token UUID, p_reconciliation_token UUID,
    p_request_hash TEXT, p_terminal_state TEXT, p_provider_receipt JSONB,
    p_result JSONB, p_cost_kind TEXT, p_reserved_amount BIGINT,
    p_actual_amount BIGINT, p_currency TEXT, p_reason_code TEXT,
    p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_action_attempts%ROWTYPE; act agent_actions%ROWTYPE;
        intent_ok BOOLEAN; cost_result JSONB; terminal_result JSONB;
        effective_token UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_terminal_state NOT IN ('completed','failed','cancelled') THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_STATE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_FINALIZE_ATTEMPT_NOT_FOUND' USING ERRCODE='22023'; END IF;
    SELECT * INTO act FROM agent_actions WHERE id=a.action_id FOR UPDATE;
    effective_token := CASE WHEN a.status IN ('accepted','unknown') THEN p_reconciliation_token ELSE p_execution_token END;
    IF effective_token IS NULL OR (a.status IN ('accepted','unknown') AND a.reconciliation_token IS DISTINCT FROM effective_token)
       OR (a.status NOT IN ('accepted','unknown') AND a.execution_token IS DISTINCT FROM effective_token)
       OR a.request_hash IS DISTINCT FROM p_request_hash THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_FENCED' USING ERRCODE='42501';
    END IF;
    IF a.status NOT IN ('dispatching','accepted','unknown') OR act.status NOT IN ('running','accepted','unknown') THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
    END IF;
    SELECT EXISTS (
        SELECT 1 FROM agent_action_dispatch_intents i
        JOIN agent_policy_receipts r ON r.id=i.policy_receipt_id
        WHERE i.attempt_id=a.id AND i.action_id=a.action_id
          AND i.request_hash=p_request_hash AND i.execution_token=a.execution_token
          AND r.action_id=a.action_id AND r.expires_at > clock_timestamp()
    ) INTO intent_ok;
    IF NOT intent_ok THEN RAISE EXCEPTION 'AGENT_FINALIZE_DISPATCH_CONTRACT_MISSING' USING ERRCODE='42501'; END IF;
    IF jsonb_typeof(COALESCE(p_provider_receipt,'{}')) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_PROVIDER_RECEIPT_INVALID' USING ERRCODE='22023';
    END IF;
    UPDATE agent_action_attempts SET external_receipt=COALESCE(p_provider_receipt,'{}'),
        last_provider_status=p_terminal_state, updated_at=clock_timestamp()
     WHERE id=a.id;
    IF p_cost_kind IS NOT NULL THEN
        IF p_cost_kind NOT IN ('settle','release','refund','adjustment') THEN
            RAISE EXCEPTION 'AGENT_FINALIZE_COST_KIND_INVALID' USING ERRCODE='22023';
        END IF;
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
            act.model_step_id,a.action_id,'executor',session_user,
            jsonb_build_object('request_hash',p_request_hash),ARRAY['web_runtime','audit']::TEXT[]);
        terminal_result := jsonb_build_object('outcome','cancelled','action_id',a.action_id);
    END IF;
    RETURN jsonb_build_object('outcome',p_terminal_state,'cost',COALESCE(cost_result,'{}'::JSONB),
        'terminal',terminal_result);
END; $$;

REVOKE ALL ON FUNCTION finalize_agent_action_provider(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finalize_agent_action_provider(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
