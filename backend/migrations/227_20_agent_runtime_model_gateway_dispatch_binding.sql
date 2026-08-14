-- C7-BG3.5: atomically bind ModelAttempt dispatch to one Gateway operation.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_model_gateway_dispatch_fences(
 p_org_id UUID,p_provider TEXT,p_purpose TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g agent_runtime_tenant_gate_controls%ROWTYPE;
 tenant_epoch BIGINT:=0; provider_epoch BIGINT:=0; capability_epoch BIGINT:=0;
BEGIN
 IF NULLIF(btrim(p_provider),'') IS NULL OR NULLIF(btrim(p_purpose),'') IS NULL THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_DISPATCH_INVALID' USING ERRCODE='22023';
 END IF;
 IF p_org_id IS NULL THEN
  RETURN jsonb_build_object('outcome','allowed','tenant_kill_epoch',0,
   'provider_kill_epoch',0,'capability_kill_epoch',0);
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::text||':tenant:tenant',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
 IF FOUND THEN
  tenant_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::text||':provider:'||btrim(p_provider),0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='provider' AND scope_key=btrim(p_provider);
 IF FOUND THEN
  provider_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::text||':capability:'||btrim(p_purpose),0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='capability' AND scope_key=btrim(p_purpose);
 IF FOUND THEN
  capability_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 RETURN jsonb_build_object('outcome','allowed','tenant_kill_epoch',tenant_epoch,
  'provider_kill_epoch',provider_epoch,'capability_kill_epoch',capability_epoch);
END $$;

CREATE FUNCTION start_agent_runtime_model_gateway_dispatch(
 p_request_id UUID,p_session_id UUID,p_run_id UUID,p_model_step_id UUID,
 p_model_attempt_id UUID,p_run_execution_token UUID,p_request_hash TEXT,
 p_expected_attempt_version BIGINT,p_model_id TEXT,p_provider TEXT,
 p_provider_revision TEXT,p_model_revision TEXT,p_purpose TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 s agent_model_steps%ROWTYPE; a agent_model_attempts%ROWTYPE;
 o agent_runtime_model_gateway_operations%ROWTYPE;
 candidate agent_runtime_model_gateway_operations%ROWTYPE; fences JSONB;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('runtime');
 IF p_request_id IS NULL OR p_session_id IS NULL OR p_run_id IS NULL
 OR p_model_step_id IS NULL OR p_model_attempt_id IS NULL
 OR p_run_execution_token IS NULL OR p_expected_attempt_version<0
 OR COALESCE(p_request_hash,'')!~'^[0-9a-f]{64}$'
 OR length(btrim(COALESCE(p_model_id,''))) NOT BETWEEN 1 AND 200
 OR length(btrim(COALESCE(p_provider,''))) NOT BETWEEN 1 AND 100
 OR length(btrim(COALESCE(p_provider_revision,''))) NOT BETWEEN 1 AND 200
 OR length(btrim(COALESCE(p_model_revision,''))) NOT BETWEEN 1 AND 200
 OR length(btrim(COALESCE(p_purpose,''))) NOT BETWEEN 1 AND 100 THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_DISPATCH_INVALID' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-model-gateway:'||p_request_id::text,0));
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=p_session_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=p_run_id FOR UPDATE;
 SELECT * INTO s FROM agent_model_steps WHERE id=p_model_step_id FOR UPDATE;
 SELECT * INTO a FROM agent_model_attempts WHERE id=p_model_attempt_id FOR UPDATE;
 IF ss.id IS NULL OR r.id IS NULL OR s.id IS NULL OR a.id IS NULL THEN
  RETURN jsonb_build_object('outcome','not_found');
 END IF;
 FOR candidate IN SELECT * FROM agent_runtime_model_gateway_operations
  WHERE request_id=p_request_id OR model_attempt_id=p_model_attempt_id
  ORDER BY id FOR UPDATE LOOP
  IF o.id IS NULL THEN o:=candidate;
  ELSIF o.id<>candidate.id THEN
   RETURN jsonb_build_object('outcome','idempotency_conflict');
  END IF;
 END LOOP;
 IF o.id IS NOT NULL THEN
  IF o.request_id IS DISTINCT FROM p_request_id
  OR o.session_id IS DISTINCT FROM p_session_id OR o.run_id IS DISTINCT FROM p_run_id
  OR o.model_step_id IS DISTINCT FROM p_model_step_id
  OR o.model_attempt_id IS DISTINCT FROM p_model_attempt_id
  OR o.org_id IS DISTINCT FROM a.org_id OR o.user_id IS DISTINCT FROM a.user_id
  OR o.runtime_worker_id IS DISTINCT FROM a.worker_id
  OR o.execution_token IS DISTINCT FROM p_run_execution_token
  OR o.request_hash IS DISTINCT FROM p_request_hash
  OR o.attempt_state_version<>p_expected_attempt_version+1
  OR o.model_id IS DISTINCT FROM btrim(p_model_id)
  OR o.provider IS DISTINCT FROM btrim(p_provider)
  OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
  OR o.model_revision IS DISTINCT FROM btrim(p_model_revision)
  OR o.purpose IS DISTINCT FROM btrim(p_purpose)
  OR a.status<>'dispatching' OR a.dispatch_phase<>'request_started'
  OR a.state_version<>o.attempt_state_version THEN
   RETURN jsonb_build_object('outcome','idempotency_conflict');
  END IF;
  RETURN jsonb_build_object('outcome','already_dispatching','attempt_id',a.id,
   'status',a.status,'dispatch_phase',a.dispatch_phase,'state_version',a.state_version,
   'worker_id',a.worker_id,'operation',_agent_model_gateway_public(o));
 END IF;
 IF r.session_id IS DISTINCT FROM ss.id OR s.session_id IS DISTINCT FROM ss.id
 OR a.session_id IS DISTINCT FROM ss.id OR s.run_id IS DISTINCT FROM r.id
 OR a.run_id IS DISTINCT FROM r.id OR a.model_step_id IS DISTINCT FROM s.id
 OR r.org_id IS DISTINCT FROM ss.org_id OR s.org_id IS DISTINCT FROM ss.org_id
 OR a.org_id IS DISTINCT FROM ss.org_id OR r.user_id IS DISTINCT FROM ss.user_id
 OR s.user_id IS DISTINCT FROM ss.user_id OR a.user_id IS DISTINCT FROM ss.user_id
 OR a.user_id IS NULL OR r.status<>'running'
 OR r.execution_token IS DISTINCT FROM p_run_execution_token
 OR a.execution_token IS DISTINCT FROM p_run_execution_token
 OR r.lease_expires_at<=clock_timestamp() OR a.lease_expires_at<=clock_timestamp()
 OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra WHERE ra.run_id=r.id
   AND ra.execution_token=p_run_execution_token AND ra.worker_id=a.worker_id
   AND ra.ended_at IS NULL AND ra.lease_expires_at>clock_timestamp())
 OR s.status<>'running' OR a.status<>'prepared' OR a.dispatch_phase<>'prepared'
 OR a.state_version<>p_expected_attempt_version
 OR a.request_hash IS DISTINCT FROM p_request_hash
 OR s.model_id IS DISTINCT FROM btrim(p_model_id)
 OR s.provider IS DISTINCT FROM btrim(p_provider)
 OR s.model_revision IS DISTINCT FROM btrim(p_model_revision)
 OR a.provider IS DISTINCT FROM btrim(p_provider)
 OR a.request_receipt->>'credential_provider' IS DISTINCT FROM btrim(p_provider)
 OR a.request_receipt->>'credential_revision' IS DISTINCT FROM btrim(p_provider_revision)
 OR a.request_receipt->>'credential_purpose' IS DISTINCT FROM btrim(p_purpose) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 fences:=_agent_model_gateway_dispatch_fences(a.org_id,btrim(p_provider),btrim(p_purpose));
 IF fences->>'outcome'<>'allowed' THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 UPDATE agent_model_attempts SET status='dispatching',dispatch_phase='request_started',
  state_version=state_version+1,dispatched_at=clock_timestamp(),updated_at=clock_timestamp()
  WHERE id=a.id RETURNING * INTO a;
 INSERT INTO agent_runtime_model_gateway_operations(
  request_id,org_id,user_id,session_id,run_id,model_step_id,model_attempt_id,
  runtime_worker_id,execution_token,request_hash,model_id,provider,provider_revision,
  model_revision,purpose,tenant_kill_epoch,provider_kill_epoch,
  capability_kill_epoch,attempt_state_version)
 VALUES(p_request_id,a.org_id,a.user_id,a.session_id,a.run_id,a.model_step_id,a.id,
  a.worker_id,a.execution_token,a.request_hash,btrim(p_model_id),btrim(p_provider),
  btrim(p_provider_revision),btrim(p_model_revision),btrim(p_purpose),
  (fences->>'tenant_kill_epoch')::BIGINT,
  (fences->>'provider_kill_epoch')::BIGINT,
  (fences->>'capability_kill_epoch')::BIGINT,a.state_version)
 RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','dispatching','attempt_id',a.id,
  'status',a.status,'dispatch_phase',a.dispatch_phase,'state_version',a.state_version,
  'worker_id',a.worker_id,'operation',_agent_model_gateway_public(o));
END $$;

CREATE FUNCTION claim_agent_runtime_model_gateway_operation_v2(
 p_request_id UUID,p_gateway_worker_id TEXT,p_runtime_worker_id TEXT,p_org_id UUID,
 p_user_id UUID,p_run_id UUID,p_model_attempt_id UUID,p_execution_token UUID,
 p_request_hash TEXT,p_attempt_state_version BIGINT,p_model_id TEXT,p_provider TEXT,
 p_provider_revision TEXT,p_model_revision TEXT,p_purpose TEXT,p_tenant_kill_epoch BIGINT,
 p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT,
 p_lease_seconds INTEGER DEFAULT 120) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE;
 ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 s agent_model_steps%ROWTYPE; a agent_model_attempts%ROWTYPE;
 t UUID; b TEXT; bundle JSONB;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway');
 IF p_lease_seconds NOT BETWEEN 15 AND 600
 OR NULLIF(btrim(p_gateway_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_CLAIM_INVALID' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-model-gateway:'||p_request_id::text,0));
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE request_id=p_request_id;
 IF o.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=o.session_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=o.run_id FOR UPDATE;
 SELECT * INTO s FROM agent_model_steps WHERE id=o.model_step_id FOR UPDATE;
 SELECT * INTO a FROM agent_model_attempts WHERE id=o.model_attempt_id FOR UPDATE;
 SELECT * INTO o FROM agent_runtime_model_gateway_operations
  WHERE request_id=p_request_id FOR UPDATE;
 IF ss.id IS NULL OR r.id IS NULL OR s.id IS NULL OR a.id IS NULL OR o.id IS NULL
 OR o.org_id IS DISTINCT FROM p_org_id OR o.user_id IS DISTINCT FROM p_user_id
 OR o.run_id IS DISTINCT FROM p_run_id
 OR o.model_attempt_id IS DISTINCT FROM p_model_attempt_id
 OR o.execution_token IS DISTINCT FROM p_execution_token
 OR o.runtime_worker_id IS DISTINCT FROM btrim(p_runtime_worker_id)
 OR o.request_hash IS DISTINCT FROM p_request_hash
 OR o.attempt_state_version<>p_attempt_state_version
 OR o.model_id IS DISTINCT FROM btrim(p_model_id)
 OR o.provider IS DISTINCT FROM btrim(p_provider)
 OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
 OR o.model_revision IS DISTINCT FROM btrim(p_model_revision)
 OR o.purpose IS DISTINCT FROM btrim(p_purpose)
 OR o.tenant_kill_epoch<>p_tenant_kill_epoch
 OR o.provider_kill_epoch<>p_provider_kill_epoch
 OR o.capability_kill_epoch<>p_capability_kill_epoch
 OR r.session_id IS DISTINCT FROM ss.id OR s.session_id IS DISTINCT FROM ss.id
 OR a.session_id IS DISTINCT FROM ss.id OR s.run_id IS DISTINCT FROM r.id
 OR a.run_id IS DISTINCT FROM r.id OR a.model_step_id IS DISTINCT FROM s.id
 OR a.org_id IS DISTINCT FROM o.org_id OR a.user_id IS DISTINCT FROM o.user_id
 OR r.status<>'running' OR r.execution_token IS DISTINCT FROM p_execution_token
 OR r.lease_expires_at<=clock_timestamp()
 OR s.status<>'running' OR s.model_id IS DISTINCT FROM o.model_id
 OR s.provider IS DISTINCT FROM o.provider OR s.model_revision IS DISTINCT FROM o.model_revision
 OR a.worker_id IS DISTINCT FROM o.runtime_worker_id
 OR a.execution_token IS DISTINCT FROM p_execution_token
 OR a.request_hash IS DISTINCT FROM p_request_hash
 OR a.status<>'dispatching' OR a.dispatch_phase<>'request_started'
 OR a.state_version<>o.attempt_state_version OR a.lease_expires_at<=clock_timestamp()
 OR a.request_receipt->>'credential_provider' IS DISTINCT FROM o.provider
 OR a.request_receipt->>'credential_revision' IS DISTINCT FROM o.provider_revision
 OR a.request_receipt->>'credential_purpose' IS DISTINCT FROM o.purpose
 OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra WHERE ra.run_id=r.id
  AND ra.execution_token=p_execution_token AND ra.worker_id=o.runtime_worker_id
  AND ra.ended_at IS NULL AND ra.lease_expires_at>clock_timestamp())
 OR NOT _agent_model_gateway_fences(p_org_id,o.provider,o.purpose,
  p_tenant_kill_epoch,p_provider_kill_epoch,p_capability_kill_epoch,'claim') THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 IF o.status IN('dispatching','completed','failed','unknown') THEN
  RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o));
 END IF;
 IF o.status='claimed' AND o.lease_expires_at>clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','busy','operation',_agent_model_gateway_public(o));
 END IF;
 t:=gen_random_uuid();
 UPDATE agent_runtime_model_gateway_operations SET status='claimed',
  lease_owner=btrim(p_gateway_worker_id),lease_token=t,
  lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
  claimed_at=clock_timestamp(),state_version=state_version+1,
  updated_at=clock_timestamp() WHERE id=o.id RETURNING * INTO o;
 b:=CASE o.provider WHEN 'dashscope' THEN 'ai.provider.dashscope'
  WHEN 'openrouter' THEN 'ai.provider.openrouter' WHEN 'kie' THEN 'ai.provider.kie'
  WHEN 'google' THEN 'ai.provider.google' END;
 IF b IS NULL THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_PROVIDER_UNSUPPORTED' USING ERRCODE='22023';
 END IF;
 bundle:=_resolve_configuration_bundle('v1',b,o.user_id,o.org_id);
 RETURN jsonb_build_object('outcome','claimed','claim_token',t,
  'operation',_agent_model_gateway_public(o),
  'input_receipt',jsonb_build_object('request_hash',a.request_hash,
   'schema_version',a.request_receipt->'schema_version',
   'prefix_hash',a.request_receipt->>'prefix_hash',
   'message_count',a.request_receipt->'message_count',
   'tool_count',a.request_receipt->'tool_count'),
  'encrypted_configuration_bundle',bundle);
END $$;

REVOKE ALL ON FUNCTION _agent_model_gateway_dispatch_fences(UUID,TEXT,TEXT),
 start_agent_runtime_model_gateway_dispatch(UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT),
 claim_agent_runtime_model_gateway_operation_v2(UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_agent_model_gateway,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
REVOKE ALL ON FUNCTION submit_agent_runtime_model_gateway_operation(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT)
FROM everydayai_agent_runtime_worker,everydayai_agent_model_gateway;
REVOKE ALL ON FUNCTION claim_agent_runtime_model_gateway_operation(
 UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER)
FROM everydayai_agent_runtime_worker,everydayai_agent_model_gateway;
GRANT EXECUTE ON FUNCTION start_agent_runtime_model_gateway_dispatch(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT)
TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_model_gateway_operation_v2(
 UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER)
TO everydayai_agent_model_gateway;

RESET ROLE;
