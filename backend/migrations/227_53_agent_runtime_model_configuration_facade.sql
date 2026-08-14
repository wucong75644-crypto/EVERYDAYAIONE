-- Final single-Runtime model dispatch and configuration boundary.
-- ModelAttempt remains the only durable model-call fact.
SET LOCAL ROLE everydayai_owner;

ALTER TABLE agent_model_attempts
 ADD COLUMN model_tenant_kill_epoch BIGINT
   CHECK(model_tenant_kill_epoch IS NULL OR model_tenant_kill_epoch>=0),
 ADD COLUMN model_provider_kill_epoch BIGINT
   CHECK(model_provider_kill_epoch IS NULL OR model_provider_kill_epoch>=0),
 ADD COLUMN model_capability_kill_epoch BIGINT
   CHECK(model_capability_kill_epoch IS NULL OR model_capability_kill_epoch>=0);

CREATE FUNCTION _agent_runtime_model_dispatch_fences_v1(
 p_org_id UUID,p_provider TEXT,p_purpose TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE g agent_runtime_tenant_gate_controls%ROWTYPE;
 tenant_epoch BIGINT:=0; provider_epoch BIGINT:=0; capability_epoch BIGINT:=0;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF NULLIF(btrim(p_provider),'') IS NULL
 OR NULLIF(btrim(p_purpose),'') IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_DISPATCH_INVALID' USING ERRCODE='22023';
 END IF;
 IF p_org_id IS NULL THEN
  RETURN jsonb_build_object('outcome','allowed','tenant_kill_epoch',0,
   'provider_kill_epoch',0,'capability_kill_epoch',0);
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::TEXT||':tenant:tenant',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
 IF FOUND THEN
  tenant_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::TEXT||':provider:'||btrim(p_provider),0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='provider' AND scope_key=btrim(p_provider);
 IF FOUND THEN
  provider_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'agent-runtime-kill-gate:'||p_org_id::TEXT||':capability:'||btrim(p_purpose),0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=p_org_id AND gate_scope='capability' AND scope_key=btrim(p_purpose);
 IF FOUND THEN
  capability_epoch:=g.kill_epoch;
  IF g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 END IF;
 RETURN jsonb_build_object('outcome','allowed',
  'tenant_kill_epoch',tenant_epoch,'provider_kill_epoch',provider_epoch,
  'capability_kill_epoch',capability_epoch);
END $$;

CREATE FUNCTION start_model_attempt_dispatch_v2(
 p_attempt_id UUID,p_run_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 s agent_model_steps%ROWTYPE; a agent_model_attempts%ROWTYPE; fences JSONB;
 provider_revision TEXT; purpose TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_attempt_id IS NULL OR p_run_execution_token IS NULL
 OR p_expected_attempt_version IS NULL OR p_expected_attempt_version<0
 OR COALESCE(p_request_hash,'')!~'^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_DISPATCH_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO a FROM agent_model_attempts WHERE id=p_attempt_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=a.session_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=a.run_id FOR UPDATE;
 SELECT * INTO s FROM agent_model_steps WHERE id=a.model_step_id FOR UPDATE;
 SELECT * INTO a FROM agent_model_attempts WHERE id=p_attempt_id FOR UPDATE;
 provider_revision:=NULLIF(btrim(a.request_receipt->>'credential_revision'),'');
 purpose:=NULLIF(btrim(a.request_receipt->>'credential_purpose'),'');
 IF ss.id IS NULL OR r.id IS NULL OR s.id IS NULL
 OR r.session_id IS DISTINCT FROM ss.id OR s.session_id IS DISTINCT FROM ss.id
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
 OR s.status<>'running' OR a.request_hash IS DISTINCT FROM p_request_hash
 OR s.provider IS DISTINCT FROM a.provider
 OR a.request_receipt->>'credential_provider' IS DISTINCT FROM a.provider
 OR a.request_receipt->>'credential_revision' IS DISTINCT FROM s.model_revision
 OR provider_revision IS NULL OR purpose IS DISTINCT FROM 'model.invoke'
 OR NULLIF(btrim(s.model_id),'') IS NULL
 OR NULLIF(btrim(s.model_revision),'') IS NULL THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 fences:=_agent_runtime_model_dispatch_fences_v1(a.org_id,a.provider,purpose);
 IF fences->>'outcome'<>'allowed' THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF a.status='dispatching' THEN
  IF a.dispatch_phase<>'request_started'
  OR a.model_tenant_kill_epoch IS DISTINCT FROM (fences->>'tenant_kill_epoch')::BIGINT
  OR a.model_provider_kill_epoch IS DISTINCT FROM (fences->>'provider_kill_epoch')::BIGINT
  OR a.model_capability_kill_epoch IS DISTINCT FROM (fences->>'capability_kill_epoch')::BIGINT THEN
   RETURN jsonb_build_object('outcome','fenced');
  END IF;
  RETURN jsonb_build_object('outcome','already_dispatching','attempt_id',a.id,
   'status',a.status,'dispatch_phase',a.dispatch_phase,'state_version',a.state_version);
 END IF;
 IF a.status<>'prepared' OR a.dispatch_phase<>'prepared'
 OR a.state_version<>p_expected_attempt_version THEN
  RETURN jsonb_build_object('outcome','stale_version');
 END IF;
 UPDATE agent_model_attempts SET status='dispatching',dispatch_phase='request_started',
  state_version=state_version+1,
  model_tenant_kill_epoch=(fences->>'tenant_kill_epoch')::BIGINT,
  model_provider_kill_epoch=(fences->>'provider_kill_epoch')::BIGINT,
  model_capability_kill_epoch=(fences->>'capability_kill_epoch')::BIGINT,
  dispatched_at=clock_timestamp(),updated_at=clock_timestamp()
 WHERE id=a.id RETURNING * INTO a;
 RETURN jsonb_build_object('outcome','dispatching','attempt_id',a.id,
  'status',a.status,'dispatch_phase',a.dispatch_phase,'state_version',a.state_version);
END $$;

CREATE FUNCTION get_agent_runtime_model_configuration_v1(
 p_run_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT,p_bundle_name TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 s agent_model_steps%ROWTYPE; a agent_model_attempts%ROWTYPE; fences JSONB;
 expected_bundle TEXT; purpose TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_expected_attempt_version IS NULL OR p_expected_attempt_version<0 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_CONFIGURATION_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO a FROM agent_model_attempts WHERE id=p_attempt_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_CONFIGURATION_SCOPE_INVALID' USING ERRCODE='42501';
 END IF;
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=a.session_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=a.run_id FOR UPDATE;
 SELECT * INTO s FROM agent_model_steps WHERE id=a.model_step_id FOR UPDATE;
 SELECT * INTO a FROM agent_model_attempts WHERE id=p_attempt_id FOR UPDATE;
 expected_bundle:=CASE a.provider
  WHEN 'dashscope' THEN 'ai.provider.dashscope'
  WHEN 'openrouter' THEN 'ai.provider.openrouter'
  WHEN 'kie' THEN 'ai.provider.kie'
  WHEN 'google' THEN 'ai.provider.google' END;
 purpose:=NULLIF(btrim(a.request_receipt->>'credential_purpose'),'');
 IF expected_bundle IS NULL OR p_bundle_name IS DISTINCT FROM expected_bundle
 OR ss.id IS NULL OR r.id IS NULL OR s.id IS NULL
 OR r.id IS DISTINCT FROM p_run_id OR r.session_id IS DISTINCT FROM ss.id
 OR s.session_id IS DISTINCT FROM ss.id OR a.session_id IS DISTINCT FROM ss.id
 OR s.run_id IS DISTINCT FROM r.id OR a.run_id IS DISTINCT FROM r.id
 OR a.model_step_id IS DISTINCT FROM s.id OR r.org_id IS DISTINCT FROM ss.org_id
 OR s.org_id IS DISTINCT FROM ss.org_id OR a.org_id IS DISTINCT FROM ss.org_id
 OR r.user_id IS DISTINCT FROM ss.user_id OR s.user_id IS DISTINCT FROM ss.user_id
 OR a.user_id IS DISTINCT FROM ss.user_id OR a.user_id IS NULL
 OR r.status<>'running' OR s.status<>'running' OR a.status<>'dispatching'
 OR a.dispatch_phase<>'request_started'
 OR r.execution_token IS DISTINCT FROM p_execution_token
 OR a.execution_token IS DISTINCT FROM p_execution_token
 OR a.worker_id IS DISTINCT FROM btrim(p_worker_id)
 OR a.state_version<>p_expected_attempt_version
 OR a.request_hash IS DISTINCT FROM p_request_hash
 OR r.lease_expires_at<=clock_timestamp() OR a.lease_expires_at<=clock_timestamp()
 OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra WHERE ra.run_id=r.id
   AND ra.execution_token=p_execution_token AND ra.worker_id=a.worker_id
   AND ra.ended_at IS NULL AND ra.lease_expires_at>clock_timestamp())
 OR s.provider IS DISTINCT FROM a.provider
 OR a.request_receipt->>'credential_provider' IS DISTINCT FROM a.provider
 OR a.request_receipt->>'credential_revision' IS DISTINCT FROM s.model_revision
 OR purpose IS DISTINCT FROM 'model.invoke' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_CONFIGURATION_SCOPE_INVALID' USING ERRCODE='42501';
 END IF;
 fences:=_agent_runtime_model_dispatch_fences_v1(a.org_id,a.provider,purpose);
 IF fences->>'outcome'<>'allowed'
 OR a.model_tenant_kill_epoch IS DISTINCT FROM (fences->>'tenant_kill_epoch')::BIGINT
 OR a.model_provider_kill_epoch IS DISTINCT FROM (fences->>'provider_kill_epoch')::BIGINT
 OR a.model_capability_kill_epoch IS DISTINCT FROM (fences->>'capability_kill_epoch')::BIGINT THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_CONFIGURATION_FENCED' USING ERRCODE='42501';
 END IF;
 RETURN _resolve_configuration_bundle('v1',expected_bundle,ss.user_id,ss.org_id);
END $$;

REVOKE ALL ON FUNCTION
 _agent_runtime_model_dispatch_fences_v1(UUID,TEXT,TEXT),
 start_model_attempt_dispatch_v2(UUID,UUID,BIGINT,TEXT),
 get_agent_runtime_model_configuration_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
 start_model_attempt_dispatch_v2(UUID,UUID,BIGINT,TEXT),
 get_agent_runtime_model_configuration_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT)
TO everydayai_agent_runtime_worker;

-- The historical Gateway role is required while traversing frozen 227_18.
-- Revoke its exact surviving execution surface in the single-Runtime schema.
DO $$
DECLARE signature TEXT;
BEGIN
 IF to_regrole('everydayai_agent_model_gateway') IS NULL THEN RETURN; END IF;
 FOREACH signature IN ARRAY ARRAY[
  'public.read_agent_runtime_model_gateway_operation(uuid,uuid,uuid,uuid,uuid,uuid,text)',
  'public.claim_agent_runtime_model_gateway_operation_v2(uuid,text,text,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,text,text,text,bigint,bigint,bigint,integer)',
  'public.mark_agent_runtime_model_gateway_dispatched(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint)',
  'public.renew_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,integer)',
  'public.finalize_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,text,text,boolean,text,jsonb,text,text)',
  'public.recover_agent_runtime_model_gateway_operations(text,integer,integer)',
  'public.fail_agent_runtime_model_gateway_claim(uuid,uuid,bigint,uuid,uuid,text,text,bigint,bigint,bigint,text)'
 ] LOOP
  IF to_regprocedure(signature) IS NOT NULL THEN
   EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM everydayai_agent_model_gateway',signature);
  END IF;
 END LOOP;
 REVOKE USAGE ON SCHEMA public FROM everydayai_agent_model_gateway;
END $$;

RESET ROLE;
