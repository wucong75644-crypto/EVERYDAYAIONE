SET LOCAL ROLE everydayai_owner;
REVOKE ALL ON FUNCTION record_agent_sync_phase_v3(UUID,UUID,UUID,BIGINT,TIMESTAMPTZ,TEXT,TEXT,JSONB,JSONB) FROM everydayai_agent_runtime_worker;
DROP FUNCTION record_agent_sync_phase_v3(UUID,UUID,UUID,BIGINT,TIMESTAMPTZ,TEXT,TEXT,JSONB,JSONB);
CREATE FUNCTION record_agent_sync_phase_v3(
    p_action_id UUID, p_attempt_id UUID, p_ownership_token UUID,
    p_expected_state_version BIGINT, p_lease_expires_at TIMESTAMPTZ,
    p_request_hash TEXT, p_phase TEXT, p_checkpoint JSONB, p_provider_receipt JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; latest TEXT;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id AND action_id=p_action_id FOR UPDATE;
  IF NOT FOUND OR a.request_hash IS DISTINCT FROM p_request_hash
     OR a.state_version IS DISTINCT FROM p_expected_state_version
     OR p_lease_expires_at <= clock_timestamp()
     OR (a.status IN ('accepted','unknown') AND (a.reconciliation_token IS DISTINCT FROM p_ownership_token OR a.reconciliation_lease_expires_at <= clock_timestamp()))
     OR (a.status NOT IN ('accepted','unknown') AND a.execution_token IS DISTINCT FROM p_ownership_token) THEN
    RETURN jsonb_build_object('outcome','fenced');
  END IF;
  SELECT phase INTO latest FROM agent_action_sync_phase_facts WHERE action_id=a.action_id AND attempt_id=a.id ORDER BY created_at DESC LIMIT 1;
  IF (latest IS NULL AND p_phase <> 'submitted') OR (latest='submitted' AND p_phase NOT IN ('submitted','progressing','unknown')) OR (latest='progressing' AND p_phase NOT IN ('progressing','applying','unknown')) OR (latest='unknown' AND p_phase NOT IN ('unknown','progressing')) OR (latest='applying' AND p_phase NOT IN ('applying','checkpointed','unknown')) OR (latest='checkpointed' AND p_phase NOT IN ('checkpointed','completed')) OR (latest='completed' AND p_phase <> 'completed') THEN
    RETURN jsonb_build_object('outcome','phase_conflict');
  END IF;
  RETURN record_agent_sync_phase_v2(p_action_id,p_attempt_id,p_ownership_token,p_request_hash,p_phase,p_checkpoint,p_provider_receipt);
END; $$;
REVOKE ALL ON FUNCTION record_agent_sync_phase_v3(UUID,UUID,UUID,BIGINT,TIMESTAMPTZ,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_agent_sync_phase_v3(UUID,UUID,UUID,BIGINT,TIMESTAMPTZ,TEXT,TEXT,JSONB,JSONB) TO everydayai_agent_runtime_worker;
RESET ROLE;
