-- Runtime-owned ERP read configuration facade.
-- Reuses the governed erp.runtime Bundle; no second credential store is added.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_erp_read_fence_v1(
 p_org_id UUID,p_attempt_id UUID,p_execution_token UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE f agent_runtime_owner_fences%ROWTYPE;
 g agent_runtime_tenant_gate_controls%ROWTYPE;
 tenant_epoch BIGINT:=0; provider_epoch BIGINT:=0; capability_epoch BIGINT:=0;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO f FROM agent_runtime_owner_fences
  WHERE owner_kind='attempt' AND owner_id=p_attempt_id
    AND org_id=p_org_id AND execution_token=p_execution_token
  FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::TEXT||':tenant:tenant',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
 IF FOUND THEN
  tenant_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::TEXT||':provider:erp',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='provider' AND scope_key='erp';
 IF FOUND THEN
  provider_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::TEXT||
  ':capability:network.provider.read',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='capability'
    AND scope_key='network.provider.read';
 IF FOUND THEN
  capability_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 IF f.tenant_kill_epoch<>tenant_epoch
 OR f.provider_kill_epoch<>provider_epoch
 OR f.capability_kill_epoch<>capability_epoch THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 RETURN jsonb_build_object('outcome','allowed');
END $$;

CREATE FUNCTION _agent_runtime_erp_read_context_v1(
 p_attempt_id UUID,p_worker_id TEXT,p_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT,p_mode TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 x agent_actions%ROWTYPE; a agent_action_attempts%ROWTYPE; fence JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_attempt_id IS NULL OR NULLIF(btrim(p_worker_id),'') IS NULL
 OR p_execution_token IS NULL OR p_expected_attempt_version IS NULL
 OR p_expected_attempt_version<0
 OR COALESCE(p_request_hash,'')!~'^[0-9a-f]{64}$'
 OR p_mode NOT IN ('configuration','token_rotation') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_ERP_CONFIGURATION_INVALID'
   USING ERRCODE='22023';
 END IF;
 SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_ERP_CONFIGURATION_SCOPE_INVALID'
   USING ERRCODE='42501';
 END IF;
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=a.session_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=a.run_id FOR UPDATE;
 SELECT * INTO x FROM agent_actions WHERE id=a.action_id FOR UPDATE;
 SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
 IF ss.id IS NULL OR r.id IS NULL OR x.id IS NULL
 OR r.session_id IS DISTINCT FROM ss.id OR x.session_id IS DISTINCT FROM ss.id
 OR a.session_id IS DISTINCT FROM ss.id OR x.run_id IS DISTINCT FROM r.id
 OR a.run_id IS DISTINCT FROM r.id OR a.action_id IS DISTINCT FROM x.id
 OR r.org_id IS DISTINCT FROM ss.org_id OR x.org_id IS DISTINCT FROM ss.org_id
 OR a.org_id IS DISTINCT FROM ss.org_id OR ss.org_id IS NULL
 OR r.user_id IS DISTINCT FROM ss.user_id OR x.user_id IS DISTINCT FROM ss.user_id
 OR a.user_id IS DISTINCT FROM ss.user_id OR ss.user_id IS NULL
 OR x.tool_name NOT IN (
  'erp_product_query','erp_trade_query','erp_purchase_query',
  'erp_aftersales_query','erp_warehouse_query','erp_info_query'
 ) OR x.policy_decision NOT IN ('allow','preauthorized')
 OR r.status<>'running' OR a.status<>'dispatching'
 OR a.dispatch_phase<>'request_started'
 OR a.worker_id IS DISTINCT FROM btrim(p_worker_id)
 OR a.execution_token IS DISTINCT FROM p_execution_token
 OR a.request_hash IS DISTINCT FROM p_request_hash
 OR a.state_version<>p_expected_attempt_version
 OR r.lease_expires_at<=clock_timestamp()
 OR a.lease_expires_at<=clock_timestamp()
 OR NOT EXISTS(
  SELECT 1 FROM agent_action_dispatch_intents intent
  JOIN agent_policy_receipts receipt ON receipt.id=intent.policy_receipt_id
   WHERE intent.attempt_id=a.id AND intent.action_id=x.id
     AND intent.execution_token=p_execution_token
     AND intent.request_hash=p_request_hash
     AND intent.executor_type='runtime_remote_read:'||x.tool_name
     AND intent.executor_revision=1
     AND intent.recovery_mode='idempotent_replay'
     AND receipt.action_id=x.id AND receipt.decision='allow'
     AND receipt.arguments_hash=x.arguments_hash
     AND receipt.executor_type=intent.executor_type
     AND receipt.executor_revision=intent.executor_revision
     AND receipt.policy_revision=x.policy_revision
     AND receipt.expires_at>clock_timestamp()
 ) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_ERP_CONFIGURATION_SCOPE_INVALID'
   USING ERRCODE='42501';
 END IF;
 fence:=_agent_runtime_erp_read_fence_v1(
  ss.org_id,a.id,p_execution_token
 );
 IF fence->>'outcome'<>'allowed' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_ERP_CONFIGURATION_FENCED'
   USING ERRCODE='42501';
 END IF;
 RETURN jsonb_build_object(
  'org_id',ss.org_id,'user_id',ss.user_id,'action_id',x.id,
  'attempt_id',a.id,'state_version',a.state_version
 );
END $$;

CREATE FUNCTION get_agent_runtime_erp_configuration_v1(
 p_attempt_id UUID,p_worker_id TEXT,p_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE context JSONB;
BEGIN
 context:=_agent_runtime_erp_read_context_v1(
  p_attempt_id,p_worker_id,p_execution_token,p_expected_attempt_version,
  p_request_hash,'configuration'
 );
 RETURN _resolve_configuration_bundle(
  'v1','erp.runtime',(context->>'user_id')::UUID,(context->>'org_id')::UUID
 );
END $$;

CREATE FUNCTION rotate_agent_runtime_erp_token_pair_v1(
 p_attempt_id UUID,p_worker_id TEXT,p_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT,
 p_secret_envelope JSONB,p_expected_config_version BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE context JSONB;
BEGIN
 IF p_expected_config_version IS NULL OR p_expected_config_version<1
 OR jsonb_typeof(p_secret_envelope)<>'object' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_ERP_TOKEN_ROTATION_INVALID'
   USING ERRCODE='22023';
 END IF;
 context:=_agent_runtime_erp_read_context_v1(
  p_attempt_id,p_worker_id,p_execution_token,p_expected_attempt_version,
  p_request_hash,'token_rotation'
 );
 RETURN _write_configuration_entry(
  'organization',(context->>'org_id')::UUID,NULL,'v1','erp.token_pair',
  NULL,p_secret_envelope,p_expected_config_version,
  (context->>'user_id')::UUID
 );
END $$;

REVOKE ALL ON FUNCTION
 _agent_runtime_erp_read_fence_v1(UUID,UUID,UUID),
 _agent_runtime_erp_read_context_v1(UUID,TEXT,UUID,BIGINT,TEXT,TEXT),
 get_agent_runtime_erp_configuration_v1(UUID,TEXT,UUID,BIGINT,TEXT),
 rotate_agent_runtime_erp_token_pair_v1(
  UUID,TEXT,UUID,BIGINT,TEXT,JSONB,BIGINT
 )
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
 get_agent_runtime_erp_configuration_v1(UUID,TEXT,UUID,BIGINT,TEXT),
 rotate_agent_runtime_erp_token_pair_v1(
  UUID,TEXT,UUID,BIGINT,TEXT,JSONB,BIGINT
 )
TO everydayai_agent_runtime_worker;

RESET ROLE;
