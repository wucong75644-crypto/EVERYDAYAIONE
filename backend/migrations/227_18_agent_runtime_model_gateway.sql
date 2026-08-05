-- C7-BG2: durable Model Gateway operation facts and narrow owner RPCs.
SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE r pg_roles%ROWTYPE;
BEGIN
  SELECT * INTO r FROM pg_roles WHERE rolname='everydayai_agent_model_gateway';
  IF NOT FOUND OR NOT r.rolcanlogin OR r.rolinherit OR r.rolsuper
     OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
     OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) THEN
    RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_ROLE_INVALID';
  END IF;
END $$;

CREATE TABLE agent_runtime_model_gateway_operations(
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id UUID NOT NULL UNIQUE,
  org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
  model_step_id UUID NOT NULL REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
  model_attempt_id UUID NOT NULL UNIQUE REFERENCES agent_model_attempts(id) ON DELETE RESTRICT,
  runtime_worker_id TEXT NOT NULL CHECK(length(btrim(runtime_worker_id)) BETWEEN 1 AND 200),
  execution_token UUID NOT NULL,
  request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
  model_id TEXT NOT NULL CHECK(length(btrim(model_id)) BETWEEN 1 AND 200),
  provider TEXT NOT NULL CHECK(length(btrim(provider)) BETWEEN 1 AND 100),
  provider_revision TEXT NOT NULL CHECK(length(btrim(provider_revision)) BETWEEN 1 AND 200),
  model_revision TEXT NOT NULL CHECK(length(btrim(model_revision)) BETWEEN 1 AND 200),
  purpose TEXT NOT NULL CHECK(length(btrim(purpose)) BETWEEN 1 AND 100),
  tenant_kill_epoch BIGINT NOT NULL CHECK(tenant_kill_epoch>=0),
  provider_kill_epoch BIGINT NOT NULL CHECK(provider_kill_epoch>=0),
  capability_kill_epoch BIGINT NOT NULL CHECK(capability_kill_epoch>=0),
  attempt_state_version BIGINT NOT NULL CHECK(attempt_state_version>=0),
  status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN(
    'submitted','claimed','dispatching','completed','failed','unknown')),
  state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
  lease_owner TEXT CHECK(lease_owner IS NULL OR length(btrim(lease_owner)) BETWEEN 1 AND 200),
  lease_token UUID, finalize_token UUID,
  lease_expires_at TIMESTAMPTZ,
  provider_request_id TEXT CHECK(provider_request_id IS NULL OR length(btrim(provider_request_id)) BETWEEN 1 AND 500),
  response_started BOOLEAN NOT NULL DEFAULT FALSE,
  response_hash TEXT CHECK(response_hash IS NULL OR response_hash~'^[0-9a-f]{64}$'),
  usage_summary JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(usage_summary)='object' AND pg_column_size(usage_summary)<=4096),
  terminal_error_code TEXT CHECK(terminal_error_code IS NULL OR terminal_error_code~'^[A-Z0-9_]{1,200}$'),
  ambiguity_code TEXT CHECK(ambiguity_code IS NULL OR ambiguity_code~'^[A-Z0-9_]{1,200}$'),
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  claimed_at TIMESTAMPTZ, dispatching_at TIMESTAMPTZ, readback_at TIMESTAMPTZ,
  finalized_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK((status IN('claimed','dispatching') AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
     OR (status NOT IN('claimed','dispatching') AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)),
  CHECK(status<>'completed' OR (response_hash IS NOT NULL AND finalized_at IS NOT NULL)),
  CHECK(status<>'failed' OR (terminal_error_code IS NOT NULL AND finalized_at IS NOT NULL)),
  CHECK(status<>'unknown' OR (ambiguity_code IS NOT NULL AND finalized_at IS NOT NULL))
);
CREATE INDEX idx_agent_model_gateway_recovery ON agent_runtime_model_gateway_operations(lease_expires_at,id)
 WHERE status IN('claimed','dispatching');
ALTER TABLE agent_runtime_model_gateway_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_model_gateway_operations FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_model_gateway_owner_all ON agent_runtime_model_gateway_operations
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);

CREATE FUNCTION _assert_agent_model_gateway_actor(p_kind TEXT)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
 IF (p_kind='runtime' AND NOT(session_user='everydayai_agent_runtime_worker' AND current_setting('app.access_kind',true)='agent_runtime'))
 OR (p_kind='gateway' AND NOT(session_user='everydayai_agent_model_gateway' AND current_setting('app.access_kind',true)='agent_model_gateway'))
 OR p_kind NOT IN('runtime','gateway') THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
END $$;

CREATE FUNCTION _agent_model_gateway_public(o agent_runtime_model_gateway_operations)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$ SELECT jsonb_build_object(
 'operation_id',o.id,'request_id',o.request_id,'org_id',o.org_id,'user_id',o.user_id,
 'session_id',o.session_id,'run_id',o.run_id,'model_step_id',o.model_step_id,
 'model_attempt_id',o.model_attempt_id,'execution_token',o.execution_token,
 'request_hash',o.request_hash,'model_id',o.model_id,'provider',o.provider,
 'provider_revision',o.provider_revision,'model_revision',o.model_revision,'purpose',o.purpose,
 'tenant_kill_epoch',o.tenant_kill_epoch,'provider_kill_epoch',o.provider_kill_epoch,
 'capability_kill_epoch',o.capability_kill_epoch,'attempt_state_version',o.attempt_state_version,
 'status',o.status,'state_version',o.state_version,'lease_expires_at',o.lease_expires_at,
 'provider_request_id',o.provider_request_id,'response_started',o.response_started,
 'response_hash',o.response_hash,'usage_summary',o.usage_summary,
 'terminal_error_code',o.terminal_error_code,'ambiguity_code',o.ambiguity_code,
 'submitted_at',o.submitted_at,'claimed_at',o.claimed_at,'dispatching_at',o.dispatching_at,
 'readback_at',o.readback_at,'finalized_at',o.finalized_at,'updated_at',o.updated_at) $$;

CREATE FUNCTION _agent_model_gateway_fences(
 p_org UUID,p_provider TEXT,p_purpose TEXT,p_tenant BIGINT,p_provider_epoch BIGINT,
 p_capability BIGINT,p_mode TEXT) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE g agent_runtime_tenant_gate_controls%ROWTYPE; e BIGINT;
BEGIN
 IF p_mode NOT IN('submit','claim','dispatch') THEN RETURN FALSE; END IF;
 IF p_org IS NULL THEN RETURN p_tenant=0 AND p_provider_epoch=0 AND p_capability=0; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org::text||':tenant:tenant',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org AND gate_scope='tenant' AND scope_key='tenant';
 e:=COALESCE(g.kill_epoch,0);
 IF e<>p_tenant OR (p_mode='claim' AND COALESCE(g.claim_blocked,FALSE))
    OR (p_mode='dispatch' AND COALESCE(g.dispatch_blocked,FALSE)) THEN RETURN FALSE; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org::text||':provider:'||p_provider,0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org AND gate_scope='provider' AND scope_key=p_provider;
 e:=COALESCE(g.kill_epoch,0);
 IF e<>p_provider_epoch OR (p_mode='dispatch' AND COALESCE(g.dispatch_blocked,FALSE)) THEN RETURN FALSE; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org::text||':capability:'||p_purpose,0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org AND gate_scope='capability' AND scope_key=p_purpose;
 e:=COALESCE(g.kill_epoch,0);
 RETURN e=p_capability AND NOT(p_mode='dispatch' AND COALESCE(g.dispatch_blocked,FALSE));
END $$;

CREATE FUNCTION submit_agent_runtime_model_gateway_operation(
 p_request_id UUID,p_org_id UUID,p_user_id UUID,p_session_id UUID,p_run_id UUID,
 p_model_step_id UUID,p_model_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,
 p_attempt_state_version BIGINT,p_model_id TEXT,p_provider TEXT,p_provider_revision TEXT,
 p_model_revision TEXT,p_purpose TEXT,p_tenant_kill_epoch BIGINT,
 p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_model_attempts%ROWTYPE; s agent_model_steps%ROWTYPE; r agent_runs%ROWTYPE;
 o agent_runtime_model_gateway_operations%ROWTYPE;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('runtime');
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-model-gateway:'||p_request_id::text,0));
 SELECT * INTO r FROM agent_runs WHERE id=p_run_id;
 SELECT * INTO s FROM agent_model_steps WHERE id=p_model_step_id;
 SELECT * INTO a FROM agent_model_attempts WHERE id=p_model_attempt_id FOR UPDATE;
 IF a.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF a.session_id IS DISTINCT FROM p_session_id OR a.run_id IS DISTINCT FROM p_run_id
 OR a.model_step_id IS DISTINCT FROM p_model_step_id OR a.org_id IS DISTINCT FROM p_org_id
 OR a.user_id IS DISTINCT FROM p_user_id OR r.session_id IS DISTINCT FROM p_session_id
 OR s.run_id IS DISTINCT FROM p_run_id OR s.session_id IS DISTINCT FROM p_session_id
 OR s.org_id IS DISTINCT FROM p_org_id OR s.user_id IS DISTINCT FROM p_user_id
 OR a.execution_token IS DISTINCT FROM p_execution_token OR r.execution_token IS DISTINCT FROM p_execution_token
 OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra WHERE ra.run_id=r.id
      AND ra.execution_token=p_execution_token AND ra.worker_id=a.worker_id AND ra.ended_at IS NULL)
 OR a.request_hash IS DISTINCT FROM p_request_hash OR a.state_version IS DISTINCT FROM p_attempt_state_version
 OR a.status<>'prepared' OR r.status<>'running' OR r.lease_expires_at<=clock_timestamp()
 OR s.status<>'running' OR s.model_id IS DISTINCT FROM btrim(p_model_id)
 OR s.provider IS DISTINCT FROM btrim(p_provider) OR s.model_revision IS DISTINCT FROM btrim(p_model_revision)
 OR a.provider IS DISTINCT FROM btrim(p_provider)
 OR a.request_receipt->>'credential_revision' IS DISTINCT FROM btrim(p_provider_revision)
 OR a.request_receipt->>'credential_provider' IS DISTINCT FROM btrim(p_provider)
 OR a.request_receipt->>'credential_purpose' IS DISTINCT FROM btrim(p_purpose)
 OR NOT _agent_model_gateway_fences(p_org_id,btrim(p_provider),btrim(p_purpose),p_tenant_kill_epoch,p_provider_kill_epoch,p_capability_kill_epoch,'submit') THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE request_id=p_request_id OR model_attempt_id=p_model_attempt_id FOR UPDATE;
 IF FOUND THEN
  IF o.request_id IS DISTINCT FROM p_request_id OR o.org_id IS DISTINCT FROM p_org_id
   OR o.user_id IS DISTINCT FROM p_user_id OR o.session_id IS DISTINCT FROM p_session_id
   OR o.run_id IS DISTINCT FROM p_run_id OR o.model_step_id IS DISTINCT FROM p_model_step_id
   OR o.model_attempt_id IS DISTINCT FROM p_model_attempt_id
   OR o.execution_token IS DISTINCT FROM p_execution_token OR o.request_hash IS DISTINCT FROM p_request_hash
   OR o.attempt_state_version<>p_attempt_state_version OR o.model_id IS DISTINCT FROM btrim(p_model_id)
   OR o.provider IS DISTINCT FROM btrim(p_provider) OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
   OR o.model_revision IS DISTINCT FROM btrim(p_model_revision) OR o.purpose IS DISTINCT FROM btrim(p_purpose)
   OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch
   OR o.capability_kill_epoch<>p_capability_kill_epoch THEN RETURN jsonb_build_object('outcome','idempotency_conflict'); END IF;
  RETURN jsonb_build_object('outcome','already_submitted','operation',_agent_model_gateway_public(o));
 END IF;
 INSERT INTO agent_runtime_model_gateway_operations(request_id,org_id,user_id,session_id,run_id,model_step_id,
  model_attempt_id,runtime_worker_id,execution_token,request_hash,model_id,provider,provider_revision,model_revision,
  purpose,tenant_kill_epoch,provider_kill_epoch,capability_kill_epoch,attempt_state_version)
 VALUES(p_request_id,p_org_id,p_user_id,p_session_id,p_run_id,p_model_step_id,p_model_attempt_id,a.worker_id,
  p_execution_token,p_request_hash,btrim(p_model_id),btrim(p_provider),btrim(p_provider_revision),btrim(p_model_revision),
  btrim(p_purpose),p_tenant_kill_epoch,p_provider_kill_epoch,p_capability_kill_epoch,p_attempt_state_version)
 RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','submitted','operation',_agent_model_gateway_public(o));
END $$;

CREATE FUNCTION read_agent_runtime_model_gateway_operation(
 p_request_id UUID,p_org_id UUID,p_user_id UUID,p_run_id UUID,p_model_attempt_id UUID,
 p_execution_token UUID,p_request_hash TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE;
BEGIN
 IF session_user='everydayai_agent_runtime_worker' THEN PERFORM _assert_agent_model_gateway_actor('runtime');
 ELSE PERFORM _assert_agent_model_gateway_actor('gateway'); END IF;
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE request_id=p_request_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.org_id IS DISTINCT FROM p_org_id OR o.user_id IS DISTINCT FROM p_user_id OR o.run_id IS DISTINCT FROM p_run_id
 OR o.model_attempt_id IS DISTINCT FROM p_model_attempt_id OR o.execution_token IS DISTINCT FROM p_execution_token
 OR o.request_hash IS DISTINCT FROM p_request_hash THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 UPDATE agent_runtime_model_gateway_operations SET readback_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','found','operation',_agent_model_gateway_public(o));
END $$;

CREATE FUNCTION claim_agent_runtime_model_gateway_operation(
 p_request_id UUID,p_gateway_worker_id TEXT,p_runtime_worker_id TEXT,p_org_id UUID,p_user_id UUID,p_run_id UUID,
 p_model_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,p_attempt_state_version BIGINT,
 p_model_id TEXT,p_provider TEXT,p_provider_revision TEXT,p_model_revision TEXT,p_purpose TEXT,
 p_tenant_kill_epoch BIGINT,p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT,
 p_lease_seconds INTEGER DEFAULT 120) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE; a agent_model_attempts%ROWTYPE;
 r agent_runs%ROWTYPE; s agent_model_steps%ROWTYPE; t UUID; b TEXT; bundle JSONB;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway');
 IF p_lease_seconds NOT BETWEEN 15 AND 600 OR NULLIF(btrim(p_gateway_worker_id),'') IS NULL THEN RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_CLAIM_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-model-gateway:'||p_request_id::text,0));
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE request_id=p_request_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=p_run_id;
 SELECT * INTO s FROM agent_model_steps WHERE id=o.model_step_id;
 SELECT * INTO a FROM agent_model_attempts WHERE id=p_model_attempt_id FOR UPDATE;
 IF o.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.org_id IS DISTINCT FROM p_org_id OR o.user_id IS DISTINCT FROM p_user_id OR o.run_id IS DISTINCT FROM p_run_id
 OR o.model_attempt_id IS DISTINCT FROM p_model_attempt_id OR o.execution_token IS DISTINCT FROM p_execution_token
 OR o.runtime_worker_id IS DISTINCT FROM btrim(p_runtime_worker_id)
 OR o.request_hash IS DISTINCT FROM p_request_hash OR o.attempt_state_version<>p_attempt_state_version
 OR o.model_id IS DISTINCT FROM btrim(p_model_id) OR o.provider IS DISTINCT FROM btrim(p_provider)
 OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision) OR o.model_revision IS DISTINCT FROM btrim(p_model_revision)
 OR o.purpose IS DISTINCT FROM btrim(p_purpose) OR o.tenant_kill_epoch<>p_tenant_kill_epoch
 OR o.provider_kill_epoch<>p_provider_kill_epoch OR o.capability_kill_epoch<>p_capability_kill_epoch
 OR r.status<>'running' OR r.execution_token IS DISTINCT FROM p_execution_token OR r.lease_expires_at<=clock_timestamp()
 OR s.status<>'running' OR s.model_id IS DISTINCT FROM o.model_id OR s.provider IS DISTINCT FROM o.provider
 OR s.model_revision IS DISTINCT FROM o.model_revision OR a.worker_id IS DISTINCT FROM o.runtime_worker_id
 OR a.execution_token IS DISTINCT FROM p_execution_token OR a.request_hash IS DISTINCT FROM p_request_hash
 OR a.state_version<>p_attempt_state_version OR a.status<>'prepared'
 OR a.request_receipt->>'credential_provider' IS DISTINCT FROM o.provider
 OR a.request_receipt->>'credential_revision' IS DISTINCT FROM o.provider_revision
 OR a.request_receipt->>'credential_purpose' IS DISTINCT FROM o.purpose
 OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra WHERE ra.run_id=r.id
      AND ra.execution_token=p_execution_token AND ra.worker_id=o.runtime_worker_id AND ra.ended_at IS NULL)
 OR NOT _agent_model_gateway_fences(p_org_id,o.provider,o.purpose,p_tenant_kill_epoch,p_provider_kill_epoch,p_capability_kill_epoch,'claim') THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF o.status IN('dispatching','completed','failed','unknown') THEN RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o)); END IF;
 IF o.status='claimed' AND o.lease_expires_at>clock_timestamp() THEN RETURN jsonb_build_object('outcome','busy','operation',_agent_model_gateway_public(o)); END IF;
 t:=gen_random_uuid();
 UPDATE agent_runtime_model_gateway_operations SET status='claimed',lease_owner=btrim(p_gateway_worker_id),lease_token=t,
  lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),claimed_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp()
 WHERE id=o.id RETURNING * INTO o;
 b:=CASE o.provider WHEN 'dashscope' THEN 'ai.provider.dashscope' WHEN 'openrouter' THEN 'ai.provider.openrouter'
  WHEN 'kie' THEN 'ai.provider.kie' WHEN 'google' THEN 'ai.provider.google' END;
 IF b IS NULL THEN RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_PROVIDER_UNSUPPORTED' USING ERRCODE='22023'; END IF;
 bundle:=_resolve_configuration_bundle('v1',b,o.user_id,o.org_id);
 RETURN jsonb_build_object('outcome','claimed','claim_token',t,'operation',_agent_model_gateway_public(o),
  'input_receipt',jsonb_build_object('request_hash',a.request_hash,
   'schema_version',a.request_receipt->'schema_version','prefix_hash',a.request_receipt->>'prefix_hash',
   'message_count',a.request_receipt->'message_count','tool_count',a.request_receipt->'tool_count'),
  'encrypted_configuration_bundle',bundle);
END $$;

CREATE FUNCTION mark_agent_runtime_model_gateway_dispatched(
 p_operation_id UUID,p_claim_token UUID,p_expected_operation_version BIGINT,p_execution_token UUID,p_request_hash TEXT,
 p_provider_revision TEXT,p_tenant_kill_epoch BIGINT,p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway'); SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE id=p_operation_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.lease_token IS DISTINCT FROM p_claim_token OR o.execution_token IS DISTINCT FROM p_execution_token OR o.request_hash IS DISTINCT FROM p_request_hash
 OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision) OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch
 OR o.capability_kill_epoch<>p_capability_kill_epoch THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF o.status='dispatching' AND o.state_version=p_expected_operation_version+1 THEN RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o)); END IF;
 IF o.state_version<>p_expected_operation_version OR o.lease_expires_at<=clock_timestamp()
 OR NOT _agent_model_gateway_fences(o.org_id,o.provider,o.purpose,p_tenant_kill_epoch,p_provider_kill_epoch,p_capability_kill_epoch,'dispatch') THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF o.status<>'claimed' THEN RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o)); END IF;
 UPDATE agent_runtime_model_gateway_operations SET status='dispatching',dispatching_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp() WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','dispatching','operation',_agent_model_gateway_public(o));
END $$;

CREATE FUNCTION renew_agent_runtime_model_gateway_operation(
 p_operation_id UUID,p_claim_token UUID,p_expected_operation_version BIGINT,p_execution_token UUID,p_request_hash TEXT,
 p_provider_revision TEXT,p_tenant_kill_epoch BIGINT,p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT,p_lease_seconds INTEGER DEFAULT 120)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway'); SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE id=p_operation_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF p_lease_seconds NOT BETWEEN 15 AND 600 OR o.status NOT IN('claimed','dispatching') OR o.lease_token IS DISTINCT FROM p_claim_token
 OR o.execution_token IS DISTINCT FROM p_execution_token OR o.request_hash IS DISTINCT FROM p_request_hash OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
 OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch OR o.capability_kill_epoch<>p_capability_kill_epoch
 OR o.state_version<>p_expected_operation_version OR o.lease_expires_at<=clock_timestamp()
 OR NOT _agent_model_gateway_fences(o.org_id,o.provider,o.purpose,p_tenant_kill_epoch,p_provider_kill_epoch,p_capability_kill_epoch,'claim') THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 UPDATE agent_runtime_model_gateway_operations SET lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),state_version=state_version+1,updated_at=clock_timestamp() WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','renewed','operation',_agent_model_gateway_public(o));
END $$;

CREATE FUNCTION finalize_agent_runtime_model_gateway_operation(
 p_operation_id UUID,p_claim_token UUID,p_expected_operation_version BIGINT,p_execution_token UUID,p_request_hash TEXT,
 p_provider_revision TEXT,p_tenant_kill_epoch BIGINT,p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT,
 p_terminal_status TEXT,p_provider_request_id TEXT,p_response_started BOOLEAN,p_response_hash TEXT,
 p_usage_summary JSONB,p_terminal_error_code TEXT,p_ambiguity_code TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE; bad BOOLEAN;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway'); SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE id=p_operation_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.status IN('completed','failed','unknown') THEN
  IF o.finalize_token IS DISTINCT FROM p_claim_token OR o.execution_token IS DISTINCT FROM p_execution_token
   OR o.request_hash IS DISTINCT FROM p_request_hash OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
   OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch
   OR o.capability_kill_epoch<>p_capability_kill_epoch THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
  RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o));
 END IF;
 IF o.status<>'dispatching' OR o.lease_token IS DISTINCT FROM p_claim_token OR o.execution_token IS DISTINCT FROM p_execution_token
 OR o.request_hash IS DISTINCT FROM p_request_hash OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
 OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch OR o.capability_kill_epoch<>p_capability_kill_epoch
 OR o.state_version<>p_expected_operation_version THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 bad:=jsonb_typeof(COALESCE(p_usage_summary,'{}'))<>'object' OR pg_column_size(COALESCE(p_usage_summary,'{}'))>4096
  OR COALESCE(p_usage_summary::text,'')~*'(secret|password|credential|api[_-]?key|authorization|cookie|prompt|payload|response)'
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(COALESCE(p_usage_summary,'{}')) k WHERE k NOT IN('input_tokens','output_tokens','reasoning_tokens','total_tokens','credits','unit'))
  OR EXISTS(SELECT 1 FROM jsonb_each(COALESCE(p_usage_summary,'{}')) e WHERE CASE
    WHEN e.key='unit' THEN jsonb_typeof(e.value)<>'string' OR e.value#>>'{}' NOT IN('tokens','credits')
    WHEN jsonb_typeof(e.value)<>'number' THEN TRUE
    ELSE (e.value::text)::numeric<0 OR trunc((e.value::text)::numeric)<>(e.value::text)::numeric END);
 IF p_terminal_status NOT IN('completed','failed','unknown') OR bad
 OR (p_terminal_status='completed' AND (p_response_hash IS NULL OR p_response_hash!~'^[0-9a-f]{64}$'))
 OR (p_terminal_status='failed' AND COALESCE(p_terminal_error_code,'')!~'^[A-Z0-9_]{1,200}$')
 OR (p_terminal_status='unknown' AND COALESCE(p_ambiguity_code,'')!~'^[A-Z0-9_]{1,200}$') THEN RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_FINALIZE_INVALID' USING ERRCODE='22023'; END IF;
 UPDATE agent_runtime_model_gateway_operations SET status=p_terminal_status,finalize_token=lease_token,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
  provider_request_id=NULLIF(btrim(p_provider_request_id),''),response_started=p_response_started,response_hash=p_response_hash,
  usage_summary=COALESCE(p_usage_summary,'{}'),terminal_error_code=CASE WHEN p_terminal_status='failed' THEN btrim(p_terminal_error_code) END,
  ambiguity_code=CASE WHEN p_terminal_status='unknown' THEN btrim(p_ambiguity_code) END,finalized_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp()
 WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome',p_terminal_status,'operation',_agent_model_gateway_public(o));
END $$;

CREATE FUNCTION recover_agent_runtime_model_gateway_operations(p_gateway_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 120,p_limit INTEGER DEFAULT 50)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE; items JSONB:='[]';
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway');
 IF NULLIF(btrim(p_gateway_worker_id),'') IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 600 OR p_limit NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_RECOVERY_INVALID' USING ERRCODE='22023'; END IF;
 FOR o IN SELECT * FROM agent_runtime_model_gateway_operations WHERE status IN('claimed','dispatching') AND lease_expires_at<=clock_timestamp() ORDER BY lease_expires_at,id FOR UPDATE SKIP LOCKED LIMIT p_limit LOOP
  IF o.status='claimed' THEN
   UPDATE agent_runtime_model_gateway_operations SET status='submitted',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,state_version=state_version+1,updated_at=clock_timestamp() WHERE id=o.id RETURNING * INTO o;
  ELSE
   UPDATE agent_runtime_model_gateway_operations SET status='unknown',finalize_token=lease_token,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,ambiguity_code='GATEWAY_LOST_AFTER_DISPATCH',finalized_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp() WHERE id=o.id RETURNING * INTO o;
  END IF;
  items:=items||jsonb_build_array(_agent_model_gateway_public(o));
 END LOOP;
 RETURN jsonb_build_object('outcome','recovered','operations',items);
END $$;

REVOKE ALL ON TABLE agent_runtime_model_gateway_operations FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,
 everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_agent_model_gateway;
REVOKE ALL ON FUNCTION _assert_agent_model_gateway_actor(TEXT),_agent_model_gateway_public(agent_runtime_model_gateway_operations),
 _agent_model_gateway_fences(UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT) FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_agent_model_gateway;
REVOKE ALL ON FUNCTION submit_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT),
 read_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,TEXT),
 claim_agent_runtime_model_gateway_operation(UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER),
 mark_agent_runtime_model_gateway_dispatched(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT),
 renew_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER),
 finalize_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT,TEXT,BOOLEAN,TEXT,JSONB,TEXT,TEXT),
 recover_agent_runtime_model_gateway_operations(TEXT,INTEGER,INTEGER)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_agent_runtime_worker,everydayai_agent_model_gateway,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
REVOKE EXECUTE ON FUNCTION get_agent_runtime_ai_bundle(UUID,TEXT,UUID,TEXT) FROM everydayai_agent_runtime_worker;
GRANT USAGE ON SCHEMA public TO everydayai_agent_model_gateway;
REVOKE CREATE ON SCHEMA public FROM everydayai_agent_model_gateway;
GRANT EXECUTE ON FUNCTION submit_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT),
 read_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,TEXT) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_model_gateway_operation(UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER),
 read_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,TEXT),
 mark_agent_runtime_model_gateway_dispatched(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT),
 renew_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER),
 finalize_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT,TEXT,BOOLEAN,TEXT,JSONB,TEXT,TEXT),
 recover_agent_runtime_model_gateway_operations(TEXT,INTEGER,INTEGER) TO everydayai_agent_model_gateway;
RESET ROLE;
