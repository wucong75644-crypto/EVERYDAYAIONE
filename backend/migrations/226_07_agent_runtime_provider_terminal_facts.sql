-- 226_07: durable terminal provider facts for local and remote adapters.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION record_agent_action_provider_terminal(
    p_attempt_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_state TEXT, p_result JSONB DEFAULT '{}',
    p_ambiguity_evidence JSONB DEFAULT '{}'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_state NOT IN ('completed', 'failed', 'cancelled', 'unknown') THEN
        RAISE EXCEPTION 'AGENT_PROVIDER_TERMINAL_STATE_INVALID' USING ERRCODE='22023';
    END IF;
    IF p_state='unknown' AND (p_ambiguity_evidence IS NULL OR p_ambiguity_evidence='{}'::JSONB) THEN
        RAISE EXCEPTION 'AGENT_PROVIDER_UNKNOWN_EVIDENCE_REQUIRED' USING ERRCODE='22023';
    END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND OR a.execution_token IS DISTINCT FROM p_execution_token
       OR a.request_hash IS DISTINCT FROM p_request_hash
       OR a.status NOT IN ('dispatching','accepted','unknown') THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM agent_action_dispatch_intents intent
        WHERE intent.attempt_id=a.id AND intent.action_id=a.action_id
          AND intent.execution_token=p_execution_token
          AND intent.request_hash=p_request_hash
    ) THEN
        RETURN jsonb_build_object('outcome','dispatch_contract_missing');
    END IF;
    UPDATE agent_action_attempts SET
        status=p_state,
        external_receipt=COALESCE(p_result,'{}'::JSONB),
        ambiguity_evidence=CASE WHEN p_state='unknown' THEN p_ambiguity_evidence ELSE ambiguity_evidence END,
        ended_at=CASE WHEN p_state IN ('completed','failed','cancelled') THEN clock_timestamp() ELSE NULL END,
        state_version=state_version+1, updated_at=clock_timestamp()
      WHERE id=p_attempt_id;
    UPDATE agent_actions SET status=p_state,
        completed_at=CASE WHEN p_state IN ('completed','failed','cancelled') THEN clock_timestamp() ELSE NULL END,
        state_version=state_version+1, updated_at=clock_timestamp()
      WHERE id=a.action_id;
    PERFORM _agent_runtime_226_append_action_event(
        a.action_id, 'action.provider.terminal',
        jsonb_build_object('state',p_state,'request_hash',p_request_hash)
    );
    RETURN jsonb_build_object('outcome',p_state,'attempt_id',p_attempt_id);
END; $$;

REVOKE ALL ON FUNCTION record_agent_action_provider_terminal(UUID,UUID,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_agent_action_provider_terminal(UUID,UUID,TEXT,TEXT,JSONB,JSONB) TO everydayai_agent_runtime_worker;
RESET ROLE;
