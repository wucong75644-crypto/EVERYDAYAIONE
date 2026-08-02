-- 226_11: durable ERP sync phase checkpoints and monotone recovery.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_action_sync_phase_facts (
    action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
    request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
    phase TEXT NOT NULL CHECK(phase IN ('submitted','progressing','applying','checkpointed','completed','unknown')),
    checkpoint JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(checkpoint)='object'),
    provider_receipt JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(provider_receipt)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(action_id,attempt_id,phase)
);
ALTER TABLE agent_action_sync_phase_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_action_sync_phase_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_action_sync_phase_facts_owner_all ON agent_action_sync_phase_facts FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_action_sync_phase_facts FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai_runtime;

CREATE FUNCTION record_agent_sync_phase(
    p_action_id UUID, p_attempt_id UUID, p_execution_token UUID,
    p_request_hash TEXT, p_phase TEXT, p_checkpoint JSONB,
    p_provider_receipt JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; prior INTEGER; current INTEGER;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id AND action_id=p_action_id FOR UPDATE;
    IF NOT FOUND OR a.request_hash IS DISTINCT FROM p_request_hash
       OR a.execution_token IS DISTINCT FROM p_execution_token
       OR a.status NOT IN ('dispatching','accepted','unknown') THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents i JOIN agent_policy_receipts r ON r.id=i.policy_receipt_id
        WHERE i.action_id=a.action_id AND i.attempt_id=a.id AND i.request_hash=p_request_hash
          AND i.execution_token=a.execution_token AND r.action_id=a.action_id AND r.expires_at>clock_timestamp()) THEN
        RETURN jsonb_build_object('outcome','dispatch_contract_missing');
    END IF;
    current := CASE p_phase WHEN 'submitted' THEN 1 WHEN 'progressing' THEN 2 WHEN 'applying' THEN 3 WHEN 'checkpointed' THEN 4 WHEN 'completed' THEN 5 WHEN 'unknown' THEN 6 ELSE 0 END;
    IF current=0 THEN RAISE EXCEPTION 'AGENT_SYNC_PHASE_INVALID' USING ERRCODE='22023'; END IF;
    SELECT max(CASE phase WHEN 'submitted' THEN 1 WHEN 'progressing' THEN 2 WHEN 'applying' THEN 3 WHEN 'checkpointed' THEN 4 WHEN 'completed' THEN 5 WHEN 'unknown' THEN 6 END) INTO prior FROM agent_action_sync_phase_facts WHERE action_id=a.action_id AND attempt_id=a.id;
    IF prior IS NOT NULL AND current < prior THEN RAISE EXCEPTION 'AGENT_SYNC_PHASE_REGRESSION' USING ERRCODE='40001'; END IF;
    INSERT INTO agent_action_sync_phase_facts(action_id,attempt_id,request_hash,phase,checkpoint,provider_receipt)
      VALUES(a.action_id,a.id,p_request_hash,p_phase,COALESCE(p_checkpoint,'{}'),COALESCE(p_provider_receipt,'{}'))
      ON CONFLICT(action_id,attempt_id,phase) DO UPDATE SET checkpoint=EXCLUDED.checkpoint,provider_receipt=EXCLUDED.provider_receipt;
    RETURN jsonb_build_object('outcome','recorded','phase',p_phase,'action_id',a.action_id,'attempt_id',a.id);
END; $$;
REVOKE ALL ON FUNCTION record_agent_sync_phase(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_agent_sync_phase(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB) TO everydayai_agent_runtime_worker;
RESET ROLE;
