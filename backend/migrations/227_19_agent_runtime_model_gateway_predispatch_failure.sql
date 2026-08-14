-- C7-BG2.1: terminalize deterministic Gateway failures before Provider dispatch.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION fail_agent_runtime_model_gateway_claim(
 p_operation_id UUID,p_claim_token UUID,p_expected_operation_version BIGINT,p_org_id UUID,
 p_execution_token UUID,p_request_hash TEXT,p_provider_revision TEXT,p_tenant_kill_epoch BIGINT,
 p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT,p_error_code TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway');
 IF COALESCE(p_error_code,'') NOT IN(
  'GATEWAY_CONFIGURATION_UNAVAILABLE','GATEWAY_CONFIGURATION_INVALID',
  'GATEWAY_KEK_UNAVAILABLE','GATEWAY_SECRET_DECRYPT_FAILED',
  'GATEWAY_PROVIDER_UNSUPPORTED','GATEWAY_PROVIDER_BUILD_FAILED') THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_PREDISPATCH_FAILURE_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO o FROM agent_runtime_model_gateway_operations
 WHERE id=p_operation_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.org_id IS DISTINCT FROM p_org_id OR o.execution_token IS DISTINCT FROM p_execution_token
 OR o.request_hash IS DISTINCT FROM p_request_hash
 OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
 OR o.tenant_kill_epoch<>p_tenant_kill_epoch
 OR o.provider_kill_epoch<>p_provider_kill_epoch
 OR o.capability_kill_epoch<>p_capability_kill_epoch THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 IF o.status='failed' THEN
  IF o.finalize_token IS DISTINCT FROM p_claim_token THEN
   RETURN jsonb_build_object('outcome','fenced');
  END IF;
  IF o.terminal_error_code IS DISTINCT FROM p_error_code THEN
   RETURN jsonb_build_object('outcome','idempotency_conflict');
  END IF;
  RETURN jsonb_build_object('outcome','already_failed','operation',_agent_model_gateway_public(o));
 END IF;
 IF o.status<>'claimed' OR o.lease_token IS DISTINCT FROM p_claim_token
 OR o.state_version<>p_expected_operation_version OR o.lease_expires_at<=clock_timestamp()
 OR o.dispatching_at IS NOT NULL OR o.provider_request_id IS NOT NULL
 OR o.response_started OR o.response_hash IS NOT NULL
 OR NOT _agent_model_gateway_fences(o.org_id,o.provider,o.purpose,p_tenant_kill_epoch,
  p_provider_kill_epoch,p_capability_kill_epoch,'claim') THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 UPDATE agent_runtime_model_gateway_operations SET
  status='failed',terminal_error_code=p_error_code,response_started=FALSE,
  provider_request_id=NULL,response_hash=NULL,ambiguity_code=NULL,usage_summary='{}'::JSONB,
  finalize_token=p_claim_token,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
  finalized_at=clock_timestamp(),updated_at=clock_timestamp(),state_version=state_version+1
 WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','failed','operation',_agent_model_gateway_public(o));
END $$;

REVOKE ALL ON FUNCTION fail_agent_runtime_model_gateway_claim(
 UUID,UUID,BIGINT,UUID,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_agent_model_gateway,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION fail_agent_runtime_model_gateway_claim(
 UUID,UUID,BIGINT,UUID,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT)
TO everydayai_agent_model_gateway;

RESET ROLE;
