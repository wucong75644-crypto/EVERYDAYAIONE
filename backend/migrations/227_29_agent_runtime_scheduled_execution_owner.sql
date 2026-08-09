SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_runtime_scheduled_execution_profiles(
 scheduled_task_id UUID PRIMARY KEY REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 source_action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
 source_attempt_id UUID NOT NULL REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
 source_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
 agent_definition_id TEXT NOT NULL,agent_definition_revision TEXT NOT NULL,
 agent_definition_hash TEXT NOT NULL CHECK(agent_definition_hash~'^[0-9a-f]{64}$'),
 catalog_revision TEXT NOT NULL CHECK(catalog_revision~'^[0-9a-f]{64}$'),
 effective_toolset_hash TEXT NOT NULL CHECK(effective_toolset_hash~'^[0-9a-f]{64}$'),
 model_snapshot JSONB NOT NULL CHECK(jsonb_typeof(model_snapshot)='object'),
 toolset_snapshot JSONB NOT NULL CHECK(jsonb_typeof(toolset_snapshot)='object'),
 scope_snapshot JSONB NOT NULL CHECK(jsonb_typeof(scope_snapshot)='object'),
 channel TEXT NOT NULL CHECK(channel IN('web','wecom')),
 budget_snapshot JSONB NOT NULL CHECK(jsonb_typeof(budget_snapshot)='object'),
 provider_key TEXT NOT NULL,capability_key TEXT NOT NULL,
 provider_revision TEXT NOT NULL,capability_revision TEXT NOT NULL,
 request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
 state_version BIGINT NOT NULL DEFAULT 1 CHECK(state_version>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK(length(btrim(agent_definition_id)) BETWEEN 1 AND 200),
 CHECK(length(btrim(agent_definition_revision)) BETWEEN 1 AND 200)
);
CREATE TABLE agent_runtime_scheduled_run_bindings(
 scheduled_run_id UUID PRIMARY KEY REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 owner_kind TEXT NOT NULL CHECK(owner_kind IN('legacy','runtime')),
 trigger_kind TEXT NOT NULL CHECK(trigger_kind IN('scheduled','manual','retry')),
 trigger_key TEXT NOT NULL CHECK(length(btrim(trigger_key)) BETWEEN 1 AND 300),
 scheduled_for TIMESTAMPTZ,manual_request_id TEXT,
 task_revision BIGINT NOT NULL CHECK(task_revision>=0),
 context_hash TEXT NOT NULL CHECK(context_hash~'^[0-9a-f]{64}$'),
 request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
 tenant_kill_epoch BIGINT NOT NULL CHECK(tenant_kill_epoch>=0),
 provider_kill_epoch BIGINT NOT NULL DEFAULT 0 CHECK(provider_kill_epoch>=0),
 capability_kill_epoch BIGINT NOT NULL DEFAULT 0 CHECK(capability_kill_epoch>=0),
 provider_revision TEXT,capability_revision TEXT,
 runtime_command_id UUID UNIQUE REFERENCES agent_session_commands(id) ON DELETE RESTRICT,
 runtime_run_id UUID UNIQUE REFERENCES agent_runs(id) ON DELETE RESTRICT,
 owner_status TEXT NOT NULL DEFAULT 'selected' CHECK(owner_status IN(
  'selected','submitted','runtime_claimed','running','cancel_requested',
  'reconcile_required','completed','failed','cancelled')),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(scheduled_task_id,trigger_kind,trigger_key),
 CHECK((trigger_kind='manual')=(manual_request_id IS NOT NULL)),
 CHECK(manual_request_id IS NULL OR length(btrim(manual_request_id)) BETWEEN 1 AND 200),
 CHECK(owner_kind='runtime' OR(runtime_command_id IS NULL AND runtime_run_id IS NULL)),
 CHECK(runtime_run_id IS NULL OR runtime_command_id IS NOT NULL)
);
CREATE INDEX idx_runtime_scheduled_bindings_task
 ON agent_runtime_scheduled_run_bindings(scheduled_task_id,created_at DESC);
ALTER TABLE agent_runtime_scheduled_execution_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_execution_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_run_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_run_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_profiles_owner_all ON agent_runtime_scheduled_execution_profiles
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_bindings_owner_all ON agent_runtime_scheduled_run_bindings
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_runtime_scheduled_execution_profiles,
 agent_runtime_scheduled_run_bindings FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_agent_runtime_worker;

CREATE FUNCTION _agent_runtime_scheduled_owner_actor() RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_agent_runtime_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'agent_runtime' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
END $$;
CREATE FUNCTION _agent_runtime_scheduled_snapshot_safe(p JSONB) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 WITH RECURSIVE n(v) AS(SELECT p UNION ALL SELECT x.v FROM n CROSS JOIN LATERAL(
  SELECT value v FROM jsonb_each(CASE WHEN jsonb_typeof(n.v)='object' THEN n.v ELSE '{}' END)
  UNION ALL SELECT value FROM jsonb_array_elements(CASE WHEN jsonb_typeof(n.v)='array' THEN n.v ELSE '[]' END))x)
 SELECT jsonb_typeof(p)='object' AND pg_column_size(p)<=65536 AND NOT EXISTS(
  SELECT 1 FROM n CROSS JOIN LATERAL jsonb_object_keys(
   CASE WHEN jsonb_typeof(n.v)='object' THEN n.v ELSE '{}' END) k
  WHERE k~*'(^|_)(secret|token|password|api_?key|credential)($|_)')
$$;
CREATE FUNCTION _agent_runtime_scheduled_identity_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_FACT_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF (OLD.scheduled_run_id,OLD.scheduled_task_id,OLD.org_id,OLD.user_id,OLD.owner_kind,
  OLD.trigger_kind,OLD.trigger_key,OLD.scheduled_for,OLD.manual_request_id,
  OLD.task_revision,OLD.context_hash,OLD.request_hash,OLD.tenant_kill_epoch,
  OLD.provider_kill_epoch,OLD.capability_kill_epoch,OLD.provider_revision,
  OLD.capability_revision,OLD.created_at) IS DISTINCT FROM
 (NEW.scheduled_run_id,NEW.scheduled_task_id,NEW.org_id,NEW.user_id,NEW.owner_kind,
  NEW.trigger_kind,NEW.trigger_key,NEW.scheduled_for,NEW.manual_request_id,
  NEW.task_revision,NEW.context_hash,NEW.request_hash,NEW.tenant_kill_epoch,
  NEW.provider_kill_epoch,NEW.capability_kill_epoch,NEW.provider_revision,
  NEW.capability_revision,NEW.created_at) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_IDENTITY_IMMUTABLE' USING ERRCODE='55000';
 END IF;
 IF NEW.state_version<=OLD.state_version THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_VERSION_INVALID' USING ERRCODE='40001';
 END IF; RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_profile_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_execution_profiles FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();
CREATE TRIGGER runtime_scheduled_binding_identity_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_run_bindings FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_identity_immutable();

CREATE FUNCTION create_agent_runtime_scheduled_execution_profile_v1(
 p_task_id UUID,p_source_action_id UUID,p_source_run_id UUID,
 p_expected_state_version BIGINT DEFAULT 0) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;x agent_actions%ROWTYPE;a agent_action_attempts%ROWTYPE;
 r agent_runs%ROWTYPE;s agent_runtime_sessions%ROWTYPE;c agent_session_commands%ROWTYPE;
 d agent_runtime_definition_facts%ROWTYPE;cat agent_runtime_catalog_facts%ROWTYPE;
 ts agent_runtime_effective_toolset_facts%ROWTYPE;f agent_runtime_owner_fences%ROWTYPE;
 g agent_runtime_tenant_gate_controls%ROWTYPE;e agent_runtime_scheduled_execution_profiles%ROWTYPE;
 model JSONB;scope JSONB;budget JSONB;env JSONB;names TEXT[];approved TEXT[];
 provider TEXT;capability TEXT;provider_epoch BIGINT:=0;capability_epoch BIGINT:=0;tenant_epoch BIGINT:=0;
BEGIN
 PERFORM _agent_runtime_scheduled_owner_actor();
 IF p_expected_state_version<>0 THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_STALE_VERSION' USING ERRCODE='40001'; END IF;
 SELECT * INTO t FROM scheduled_tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO x FROM agent_actions WHERE id=p_source_action_id;
 SELECT * INTO a FROM agent_action_attempts WHERE id=t.runtime_attempt_id;
 SELECT * INTO r FROM agent_runs WHERE id=p_source_run_id;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
 SELECT * INTO c FROM agent_session_commands WHERE id=r.command_id;
 env:=c.payload->'run_envelope';
 IF t.id IS NULL OR x.id IS NULL OR a.id IS NULL OR r.id IS NULL OR s.id IS NULL OR c.id IS NULL
 OR t.runtime_action_id IS DISTINCT FROM x.id OR t.runtime_attempt_id IS DISTINCT FROM a.id
 OR x.run_id IS DISTINCT FROM r.id OR a.action_id IS DISTINCT FROM x.id
 OR (t.org_id,t.user_id) IS DISTINCT FROM (x.org_id,x.user_id)
 OR (t.org_id,t.user_id) IS DISTINCT FROM (r.org_id,r.user_id)
 OR (r.org_id,r.user_id) IS DISTINCT FROM (s.org_id,COALESCE(s.user_id,r.user_id))
 OR c.session_id IS DISTINCT FROM s.id OR c.org_id IS DISTINCT FROM t.org_id
 OR c.user_id IS DISTINCT FROM t.user_id OR x.request_hash IS DISTINCT FROM t.runtime_request_hash
 OR x.arguments->>'operation' IS DISTINCT FROM 'create'
 OR jsonb_typeof(x.arguments->'payload') IS DISTINCT FROM 'object'
 OR COALESCE((x.arguments->'payload'->>'max_credits')::INTEGER,10) IS DISTINCT FROM t.max_credits
 OR COALESCE((x.arguments->'payload'->>'retry_count')::INTEGER,1) IS DISTINCT FROM t.retry_count
 OR COALESCE((x.arguments->'payload'->>'timeout_sec')::INTEGER,180) IS DISTINCT FROM t.timeout_sec
 OR r.config_snapshot IS DISTINCT FROM env->'config_snapshot'
 OR r.capability_snapshot IS DISTINCT FROM env->'capability_snapshot'
 OR env->'request_identity'->>'org_id' IS DISTINCT FROM t.org_id::TEXT
 OR env->'request_identity'->>'user_id' IS DISTINCT FROM t.user_id::TEXT
 OR env->'request_identity'->>'scope_kind' IS DISTINCT FROM s.scope_kind
 OR env->'request_identity'->>'scope_id' IS DISTINCT FROM s.scope_id
 OR NOT EXISTS(SELECT 1 FROM agent_runtime_scheduler_operation_intents i
  JOIN agent_runtime_scheduler_operation_receipts q ON q.intent_id=i.id AND q.outcome='committed'
  WHERE i.task_id=t.id AND i.action_id=x.id AND i.attempt_id=a.id AND i.run_id=r.id
   AND i.org_id=t.org_id AND i.user_id=t.user_id AND i.operation='create') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_SOURCE_INVALID' USING ERRCODE='42501';
 END IF;
 model:=r.config_snapshot->'resolved_model';
 scope:=jsonb_build_object('scope_kind',s.scope_kind,'scope_id',s.scope_id,'org_id',t.org_id,'user_id',t.user_id);
 budget:=jsonb_build_object('max_credits',t.max_credits,'retry_count',t.retry_count,'timeout_sec',t.timeout_sec);
 IF NOT _agent_runtime_scheduled_snapshot_safe(model)
 OR NULLIF(model->>'model_id','') IS NULL OR NULLIF(model->>'provider','') IS NULL
 OR NULLIF(model->>'revision','') IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_MODEL_SNAPSHOT_INVALID' USING ERRCODE='42501';
 END IF;
 SELECT * INTO d FROM agent_runtime_definition_facts WHERE agent_key=s.agent_definition_id
  AND definition_revision=s.agent_definition_revision
  AND definition_hash=r.capability_snapshot->>'agent_definition_hash' AND recoverable;
 SELECT * INTO cat FROM agent_runtime_catalog_facts
  WHERE catalog_revision=r.capability_snapshot->>'tool_catalog_revision'
  AND catalog_hash=r.capability_snapshot->>'tool_catalog_hash' AND recoverable;
 SELECT * INTO ts FROM agent_runtime_effective_toolset_facts WHERE agent_key=s.agent_definition_id
  AND definition_revision=s.agent_definition_revision AND catalog_revision=cat.catalog_revision
  AND scope_kind=s.scope_kind AND channel=r.capability_snapshot->>'channel'
  AND gate_state=r.capability_snapshot->>'gate_state'
  AND effective_toolset_hash=r.capability_snapshot->>'effective_toolset_hash' AND recoverable;
 approved:=CASE WHEN s.scope_kind='user' THEN ARRAY['artifact_get','artifact_read','artifact_search','evidence_get','evidence_search','get_conversation_context','memory_get','memory_search','search_knowledge']
 ELSE ARRAY['artifact_get','artifact_read','artifact_search','evidence_get','evidence_search','get_conversation_context','local_compare_stats','local_platform_map_query','local_product_identify','local_product_stats','local_shop_list','local_stock_query','local_supplier_list','local_warehouse_list','memory_get','memory_search','search_knowledge'] END;
 SELECT array_agg(v ORDER BY v) INTO names FROM jsonb_array_elements_text(ts.toolset_document->'tool_names') v;
 IF d.agent_key IS NULL OR cat.catalog_revision IS NULL OR ts.agent_key IS NULL
 OR d.agent_key<>'everydayai-default' OR d.definition_revision<>'v4'
 OR d.prompt_revision<>'agent-runtime-safe-read-v1' OR cat.catalog_revision IS DISTINCT FROM d.catalog_revision
 OR r.capability_snapshot->>'effective_toolset_revision' IS DISTINCT FROM d.catalog_revision
 OR names IS DISTINCT FROM approved OR jsonb_array_length(ts.toolset_document->'tools')<>cardinality(approved)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(ts.toolset_document->'tools') z
  WHERE z->>'canonical_name'<>ALL(approved) OR z->>'executor_type' IS DISTINCT FROM 'runtime_read:'||(z->>'canonical_name')
   OR z->>'safety_level' IS DISTINCT FROM 'safe' OR z->>'side_effect' IS DISTINCT FROM 'none'
   OR z->>'authorization_requirement' IS DISTINCT FROM 'none') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TOOLSET_NOT_APPROVED' USING ERRCODE='42501';
 END IF;
 provider:=NULLIF(btrim(COALESCE(x.policy_snapshot->>'provider',x.policy_snapshot->>'provider_name')),'');
 capability:=NULLIF(btrim(COALESCE(x.policy_snapshot->>'capability',x.policy_snapshot->>'capability_name')),'');
 IF provider IS NULL OR capability IS NULL OR NULLIF(x.policy_snapshot->>'provider_revision','') IS NULL
 OR NULLIF(x.policy_snapshot->>'capability_revision','') IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_REVISION_INVALID' USING ERRCODE='42501'; END IF;
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=t.org_id AND gate_scope='tenant' AND scope_key='tenant';
 tenant_epoch:=COALESCE(g.kill_epoch,0);
 IF FOUND AND(g.claim_blocked OR g.dispatch_blocked) THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TENANT_BLOCKED' USING ERRCODE='42501'; END IF;
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=t.org_id AND gate_scope='provider' AND scope_key=provider;
 provider_epoch:=COALESCE(g.kill_epoch,0); IF FOUND AND g.dispatch_blocked THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROVIDER_BLOCKED' USING ERRCODE='42501'; END IF;
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=t.org_id AND gate_scope='capability' AND scope_key=capability;
 capability_epoch:=COALESCE(g.kill_epoch,0); IF FOUND AND g.dispatch_blocked THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_CAPABILITY_BLOCKED' USING ERRCODE='42501'; END IF;
 SELECT * INTO f FROM agent_runtime_owner_fences WHERE owner_kind='attempt' AND owner_id=a.id AND execution_token=a.execution_token;
 IF NOT FOUND OR f.tenant_kill_epoch<>tenant_epoch OR f.provider_kill_epoch<>provider_epoch
 OR f.capability_kill_epoch<>capability_epoch OR f.provider_revision IS DISTINCT FROM x.policy_snapshot->>'provider_revision'
 OR f.capability_revision IS DISTINCT FROM x.policy_snapshot->>'capability_revision' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_FENCED' USING ERRCODE='42501'; END IF;
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=t.id;
 IF FOUND THEN RETURN jsonb_build_object('outcome','already_exists','profile',to_jsonb(e)); END IF;
 INSERT INTO agent_runtime_scheduled_execution_profiles VALUES(t.id,t.org_id,t.user_id,x.id,a.id,r.id,
  d.agent_key,d.definition_revision,d.definition_hash,d.catalog_revision,ts.effective_toolset_hash,
  model,ts.toolset_document,scope,r.capability_snapshot->>'channel',budget,provider,capability,
  x.policy_snapshot->>'provider_revision',x.policy_snapshot->>'capability_revision',x.request_hash,1,clock_timestamp()) RETURNING * INTO e;
 RETURN jsonb_build_object('outcome','created','profile',to_jsonb(e));
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_execution_profile_v1(
 p_task_id UUID,p_org_id UUID,p_user_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e agent_runtime_scheduled_execution_profiles%ROWTYPE;
BEGIN PERFORM _agent_runtime_scheduled_owner_actor();
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=p_task_id AND org_id=p_org_id AND user_id=p_user_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found','owner_kind','legacy'); END IF;
 RETURN jsonb_build_object('outcome','found','owner_kind','runtime','profile',to_jsonb(e));
END $$;

CREATE FUNCTION select_agent_runtime_scheduled_run_owner_v1(
 p_task_id UUID,p_scheduled_run_id UUID,p_org_id UUID,p_user_id UUID,
 p_trigger_kind TEXT,p_trigger_key TEXT,p_scheduled_for TIMESTAMPTZ,
 p_manual_request_id TEXT,p_task_revision BIGINT,p_context_hash TEXT,
 p_request_hash TEXT,p_tenant_kill_epoch BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;b agent_runtime_scheduled_run_bindings%ROWTYPE;
 e agent_runtime_scheduled_execution_profiles%ROWTYPE;g agent_runtime_tenant_gate_controls%ROWTYPE;
 owner TEXT;tenant_epoch BIGINT:=0;provider_epoch BIGINT:=0;capability_epoch BIGINT:=0;
BEGIN PERFORM _agent_runtime_scheduled_owner_actor();
 IF p_trigger_kind NOT IN('scheduled','manual','retry')
 OR(p_trigger_kind='manual') IS DISTINCT FROM(p_manual_request_id IS NOT NULL)
 OR NULLIF(btrim(p_trigger_key),'') IS NULL OR p_context_hash!~'^[0-9a-f]{64}$'
 OR p_request_hash!~'^[0-9a-f]{64}$' OR p_task_revision<0 OR p_tenant_kill_epoch<0 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-run-owner:'||p_scheduled_run_id,0));
 PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-trigger-owner:'||p_task_id||':'||p_trigger_kind||':'||btrim(p_trigger_key),0));
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org_id||':tenant:tenant',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
 tenant_epoch:=COALESCE(g.kill_epoch,0);
 IF(FOUND AND(g.claim_blocked OR g.dispatch_blocked)) OR tenant_epoch<>p_tenant_kill_epoch THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TENANT_FENCED' USING ERRCODE='42501'; END IF;
 SELECT * INTO t FROM scheduled_tasks WHERE id=p_task_id FOR UPDATE;
 IF NOT FOUND OR(t.org_id,t.user_id,t.runtime_state_version) IS DISTINCT FROM(p_org_id,p_user_id,p_task_revision)
 OR NOT EXISTS(SELECT 1 FROM scheduled_task_runs q WHERE q.id=p_scheduled_run_id AND q.task_id=p_task_id AND q.org_id=p_org_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_BINDING_INVALID' USING ERRCODE='42501'; END IF;
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=t.id;
 IF FOUND THEN
  PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org_id||':provider:'||e.provider_key,0));
  SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org_id AND gate_scope='provider' AND scope_key=e.provider_key;
  provider_epoch:=COALESCE(g.kill_epoch,0); IF FOUND AND g.dispatch_blocked THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROVIDER_FENCED' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org_id||':capability:'||e.capability_key,0));
  SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org_id AND gate_scope='capability' AND scope_key=e.capability_key;
  capability_epoch:=COALESCE(g.kill_epoch,0); IF FOUND AND g.dispatch_blocked THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_CAPABILITY_FENCED' USING ERRCODE='42501'; END IF;
 END IF;
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=p_scheduled_run_id
 OR(scheduled_task_id=p_task_id AND trigger_kind=p_trigger_kind AND trigger_key=btrim(p_trigger_key))
 ORDER BY(scheduled_run_id=p_scheduled_run_id) DESC LIMIT 1;
 IF FOUND THEN
  IF b.scheduled_task_id IS DISTINCT FROM p_task_id OR b.org_id IS DISTINCT FROM p_org_id
  OR b.user_id IS DISTINCT FROM p_user_id OR b.trigger_kind IS DISTINCT FROM p_trigger_kind
  OR b.trigger_key IS DISTINCT FROM btrim(p_trigger_key) OR b.scheduled_for IS DISTINCT FROM p_scheduled_for
  OR b.manual_request_id IS DISTINCT FROM p_manual_request_id OR b.task_revision IS DISTINCT FROM p_task_revision
  OR b.context_hash IS DISTINCT FROM p_context_hash OR b.request_hash IS DISTINCT FROM p_request_hash
  OR b.tenant_kill_epoch<>tenant_epoch OR b.provider_kill_epoch<>provider_epoch
  OR b.capability_kill_epoch<>capability_epoch THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
  RETURN jsonb_build_object('outcome','already_selected','binding',to_jsonb(b));
 END IF;
 owner:=CASE WHEN e.scheduled_task_id IS NULL THEN 'legacy' ELSE 'runtime' END;
 INSERT INTO agent_runtime_scheduled_run_bindings(scheduled_run_id,scheduled_task_id,org_id,user_id,
  owner_kind,trigger_kind,trigger_key,scheduled_for,manual_request_id,task_revision,context_hash,
  request_hash,tenant_kill_epoch,provider_kill_epoch,capability_kill_epoch,provider_revision,capability_revision)
 VALUES(p_scheduled_run_id,p_task_id,p_org_id,p_user_id,owner,p_trigger_kind,btrim(p_trigger_key),
  p_scheduled_for,p_manual_request_id,p_task_revision,p_context_hash,p_request_hash,tenant_epoch,
  provider_epoch,capability_epoch,e.provider_revision,e.capability_revision) RETURNING * INTO b;
 RETURN jsonb_build_object('outcome','selected','binding',to_jsonb(b));
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_run_owner_v1(
 p_task_id UUID,p_scheduled_run_id UUID,p_org_id UUID,p_user_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE b agent_runtime_scheduled_run_bindings%ROWTYPE;
BEGIN PERFORM _agent_runtime_scheduled_owner_actor();
 IF NOT EXISTS(SELECT 1 FROM scheduled_tasks WHERE id=p_task_id AND org_id=p_org_id AND user_id=p_user_id)
 OR NOT EXISTS(SELECT 1 FROM scheduled_task_runs WHERE id=p_scheduled_run_id AND task_id=p_task_id AND org_id=p_org_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_TENANT_MISMATCH' USING ERRCODE='42501'; END IF;
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=p_scheduled_run_id;
 IF NOT FOUND AND EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=p_task_id) THEN
  RETURN jsonb_build_object('outcome','runtime_profile_unbound','owner_kind','runtime');
 END IF;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','defaulted','owner_kind','legacy'); END IF;
 RETURN jsonb_build_object('outcome','found','owner_kind',b.owner_kind,'binding',to_jsonb(b));
END $$;

CREATE FUNCTION bind_agent_runtime_scheduled_run_runtime_v1(
 p_scheduled_run_id UUID,p_runtime_command_id UUID,p_runtime_run_id UUID,
 p_expected_state_version BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE b agent_runtime_scheduled_run_bindings%ROWTYPE;e agent_runtime_scheduled_execution_profiles%ROWTYPE;
 c agent_session_commands%ROWTYPE;r agent_runs%ROWTYPE;s agent_runtime_sessions%ROWTYPE;env JSONB;identity JSONB;
BEGIN PERFORM _agent_runtime_scheduled_owner_actor();
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=p_scheduled_run_id FOR UPDATE;
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=b.scheduled_task_id;
 IF b.owner_kind IS DISTINCT FROM 'runtime' OR e.scheduled_task_id IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_RUN_NOT_RUNTIME_OWNED' USING ERRCODE='42501'; END IF;
 IF b.runtime_command_id IS NOT NULL THEN
  IF(b.runtime_command_id,b.runtime_run_id) IS DISTINCT FROM(p_runtime_command_id,p_runtime_run_id) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_RUNTIME_BINDING_CONFLICT' USING ERRCODE='40001'; END IF;
  RETURN jsonb_build_object('outcome','already_bound','binding',to_jsonb(b)); END IF;
 IF b.state_version<>p_expected_state_version THEN RETURN jsonb_build_object('outcome','stale_version','state_version',b.state_version); END IF;
 SELECT * INTO c FROM agent_session_commands WHERE id=p_runtime_command_id;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=c.session_id;
 env:=c.payload->'run_envelope';identity:=env->'request_identity';
 IF c.id IS NULL OR(c.org_id,c.user_id) IS DISTINCT FROM(b.org_id,b.user_id)
 OR(s.org_id,s.user_id,s.scope_kind,s.scope_id) IS DISTINCT FROM(b.org_id,b.user_id,'system','scheduler:'||b.scheduled_task_id)
 OR identity->>'source' IS DISTINCT FROM 'scheduler'
 OR identity->>'scheduled_task_id' IS DISTINCT FROM b.scheduled_task_id::TEXT
 OR identity->>'scheduled_run_id' IS DISTINCT FROM b.scheduled_run_id::TEXT
 OR identity->>'trigger_kind' IS DISTINCT FROM b.trigger_kind OR identity->>'trigger_key' IS DISTINCT FROM b.trigger_key
 OR identity->>'request_hash' IS DISTINCT FROM b.request_hash OR identity->>'context_hash' IS DISTINCT FROM b.context_hash
 OR identity->>'tenant_kill_epoch' IS DISTINCT FROM b.tenant_kill_epoch::TEXT
 OR identity->>'agent_definition_id' IS DISTINCT FROM e.agent_definition_id
 OR identity->>'agent_definition_revision' IS DISTINCT FROM e.agent_definition_revision
 OR identity->>'agent_definition_hash' IS DISTINCT FROM e.agent_definition_hash
 OR identity->>'catalog_revision' IS DISTINCT FROM e.catalog_revision
 OR identity->>'effective_toolset_hash' IS DISTINCT FROM e.effective_toolset_hash
 OR env->'context_receipt'->>'source' IS DISTINCT FROM 'scheduler'
 OR env->'context_receipt'->>'scheduled_run_id' IS DISTINCT FROM b.scheduled_run_id::TEXT
 OR env->'context_receipt'->>'context_hash' IS DISTINCT FROM b.context_hash
 OR env->'config_snapshot'->'resolved_model' IS DISTINCT FROM e.model_snapshot
 OR env->'config_snapshot'->'scheduled_budget' IS DISTINCT FROM e.budget_snapshot
 OR env->'capability_snapshot'->>'channel' IS DISTINCT FROM e.channel
 OR env->'capability_snapshot'->>'effective_toolset_hash' IS DISTINCT FROM e.effective_toolset_hash THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_CONTEXT_ENVELOPE_REQUIRED' USING ERRCODE='42501'; END IF;
 IF p_runtime_run_id IS NOT NULL THEN
  SELECT * INTO r FROM agent_runs WHERE id=p_runtime_run_id;
  IF r.id IS NULL OR(r.command_id,r.session_id,r.org_id,r.user_id) IS DISTINCT FROM(c.id,s.id,b.org_id,b.user_id)
  OR r.context_receipt IS DISTINCT FROM env->'context_receipt'
  OR r.config_snapshot IS DISTINCT FROM env->'config_snapshot'
  OR r.capability_snapshot IS DISTINCT FROM env->'capability_snapshot' THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_RUN_BINDING_INVALID' USING ERRCODE='42501'; END IF;
 END IF;
 UPDATE agent_runtime_scheduled_run_bindings SET runtime_command_id=c.id,runtime_run_id=p_runtime_run_id,
  owner_status=CASE WHEN p_runtime_run_id IS NULL THEN 'submitted' ELSE 'runtime_claimed' END,
  state_version=state_version+1,updated_at=clock_timestamp() WHERE scheduled_run_id=p_scheduled_run_id RETURNING * INTO b;
 RETURN jsonb_build_object('outcome','bound','binding',to_jsonb(b));
END $$;

CREATE FUNCTION _agent_runtime_scheduled_owner_gate(
 p_task_id UUID,p_scheduled_run_id UUID,p_expected_owner TEXT) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE owner TEXT;
BEGIN
 IF p_expected_owner NOT IN('legacy','runtime') THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_EXPECTED_OWNER_INVALID' USING ERRCODE='22023'; END IF;
 IF NOT EXISTS(SELECT 1 FROM scheduled_task_runs WHERE id=p_scheduled_run_id AND task_id=p_task_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_BINDING_INVALID' USING ERRCODE='42501'; END IF;
 SELECT owner_kind INTO owner FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=p_scheduled_run_id;
 IF owner IS NULL AND EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=p_task_id) THEN
  RAISE EXCEPTION 'SCHEDULED_RUN_RUNTIME_PROFILE_UNBOUND' USING ERRCODE='42501'; END IF;
 owner:=COALESCE(owner,'legacy');
 IF owner<>p_expected_owner THEN RAISE EXCEPTION 'SCHEDULED_RUN_%_OWNED',upper(owner) USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('outcome','allowed','owner_kind',owner);
END $$;
CREATE FUNCTION assert_agent_runtime_scheduled_run_owner_v1(
 p_task_id UUID,p_scheduled_run_id UUID,p_expected_owner TEXT) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN PERFORM _agent_runtime_scheduled_owner_actor();
 RETURN _agent_runtime_scheduled_owner_gate(p_task_id,p_scheduled_run_id,p_expected_owner);
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_owner_actor(),
 _agent_runtime_scheduled_snapshot_safe(JSONB),_agent_runtime_scheduled_identity_immutable(),
 _agent_runtime_scheduled_owner_gate(UUID,UUID,TEXT),
 create_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID,BIGINT),
 read_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID),
 select_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID),
 bind_agent_runtime_scheduled_run_runtime_v1(UUID,UUID,UUID,BIGINT),
 assert_agent_runtime_scheduled_run_owner_v1(UUID,UUID,TEXT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION
 create_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID,BIGINT),
 read_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID),
 select_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID),
 bind_agent_runtime_scheduled_run_runtime_v1(UUID,UUID,UUID,BIGINT),
 assert_agent_runtime_scheduled_run_owner_v1(UUID,UUID,TEXT)
 TO everydayai_agent_runtime_worker;
RESET ROLE;
