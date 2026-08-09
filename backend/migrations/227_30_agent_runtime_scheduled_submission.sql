SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_submission_control(
 singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
 mode TEXT NOT NULL DEFAULT 'disabled' CHECK(mode IN('disabled','disposable')),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO agent_runtime_scheduled_submission_control(singleton,mode) VALUES(TRUE,'disabled');
CREATE TABLE agent_runtime_scheduled_submission_intents(
 scheduled_run_id UUID PRIMARY KEY REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 requester_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 trigger_kind TEXT NOT NULL CHECK(trigger_kind IN('scheduled','manual')),
 trigger_key TEXT NOT NULL CHECK(length(btrim(trigger_key)) BETWEEN 1 AND 300),
 scheduled_for TIMESTAMPTZ, manual_request_id TEXT,
 conversation_id UUID NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE RESTRICT,
 message_id UUID NOT NULL UNIQUE REFERENCES messages(id) ON DELETE RESTRICT,
 session_id UUID NOT NULL UNIQUE REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
 command_id UUID NOT NULL UNIQUE REFERENCES agent_session_commands(id) ON DELETE RESTRICT,
 request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
 context_hash TEXT NOT NULL CHECK(context_hash~'^[0-9a-f]{64}$'),
 task_revision BIGINT NOT NULL CHECK(task_revision>=0),
 profile_state_version BIGINT NOT NULL CHECK(profile_state_version>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(scheduled_task_id,trigger_kind,trigger_key),
 CHECK((trigger_kind='manual')=(manual_request_id IS NOT NULL))
);
CREATE UNIQUE INDEX runtime_scheduled_manual_request_identity
 ON agent_runtime_scheduled_submission_intents(org_id,requester_user_id,manual_request_id)
 WHERE trigger_kind='manual';
ALTER TABLE agent_runtime_scheduled_submission_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_submission_control FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_submission_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_submission_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_submission_control_owner ON agent_runtime_scheduled_submission_control
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_submission_intents_owner ON agent_runtime_scheduled_submission_intents
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_runtime_scheduled_submission_control,
 agent_runtime_scheduled_submission_intents FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_agent_runtime_worker;
CREATE TRIGGER runtime_scheduled_submission_intent_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_submission_intents FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();

CREATE FUNCTION _agent_runtime_scheduled_submission_worker() RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'worker' THEN
  RAISE EXCEPTION 'SCHEDULED_SUBMISSION_WORKER_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
END $$;
CREATE FUNCTION _agent_runtime_scheduled_submission_enabled() RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE((SELECT mode='disposable' FROM agent_runtime_scheduled_submission_control WHERE singleton),FALSE)
$$;
CREATE FUNCTION _agent_runtime_scheduled_profile_seed(p_task_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;x agent_actions%ROWTYPE;r agent_runs%ROWTYPE;
 s agent_runtime_sessions%ROWTYPE;d agent_runtime_definition_facts%ROWTYPE;
 cat agent_runtime_catalog_facts%ROWTYPE;ts agent_runtime_effective_toolset_facts%ROWTYPE;
 approved TEXT[];tools JSONB;target JSONB;facts JSONB;canonical TEXT;toolset_hash TEXT;
BEGIN
 SELECT * INTO t FROM scheduled_tasks WHERE id=p_task_id;
 SELECT * INTO x FROM agent_actions WHERE id=t.runtime_action_id;
 SELECT * INTO r FROM agent_runs WHERE id=x.run_id;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
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
 IF t.id IS NULL OR x.id IS NULL OR r.id IS NULL OR s.id IS NULL OR d.agent_key IS NULL
 OR cat.catalog_revision IS NULL OR ts.agent_key IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_SOURCE_INCOMPLETE' USING ERRCODE='42501';
 END IF;
 approved:=CASE WHEN s.scope_kind='user' THEN
  ARRAY['artifact_get','artifact_read','artifact_search','evidence_get','evidence_search','get_conversation_context','memory_get','memory_search','search_knowledge']
 ELSE ARRAY['artifact_get','artifact_read','artifact_search','evidence_get','evidence_search','get_conversation_context','local_compare_stats','local_platform_map_query','local_product_identify','local_product_stats','local_shop_list','local_stock_query','local_supplier_list','local_warehouse_list','memory_get','memory_search','search_knowledge'] END;
 SELECT jsonb_agg(value ORDER BY value->>'canonical_name') INTO tools
 FROM jsonb_array_elements(ts.toolset_document->'tools') WHERE value->>'canonical_name'=ANY(approved);
 facts:=jsonb_build_object('agent_definition_hash',d.definition_hash,
  'catalog_revision',cat.catalog_revision,'scope_kind',s.scope_kind,
  'channel',r.capability_snapshot->>'channel','tools',tools);
 canonical:=_agent_runtime_scheduled_canonical_json(facts);
 toolset_hash:=encode(digest(convert_to(canonical,'UTF8'),'sha256'),'hex');
 target:=jsonb_build_object('scope_kind',s.scope_kind,
  'channel',r.capability_snapshot->>'channel','gate_state','enabled',
  'entitled_groups',(SELECT jsonb_agg(group_name ORDER BY group_name) FROM(
   SELECT DISTINCT value->>'tool_group' group_name FROM jsonb_array_elements(tools)
  ) groups),'tool_names',to_jsonb(approved),'tools',tools,'toolset_hash',toolset_hash);
 RETURN jsonb_build_object('source_run_id',r.id,'target',target,
  'toolset_hash',toolset_hash,'canonical',canonical);
END $$;
CREATE FUNCTION _ensure_agent_runtime_scheduled_profile(p_task_id UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE task scheduled_tasks%ROWTYPE;seed JSONB;result JSONB;runtime_bound BOOLEAN;
BEGIN
 SELECT * INTO task FROM scheduled_tasks WHERE id=p_task_id FOR UPDATE;
 runtime_bound:=task.runtime_action_id IS NOT NULL OR task.runtime_attempt_id IS NOT NULL
  OR task.runtime_request_hash IS NOT NULL OR task.runtime_idempotency_key IS NOT NULL;
 IF NOT runtime_bound THEN RETURN; END IF;
 IF task.runtime_action_id IS NULL OR task.runtime_attempt_id IS NULL
 OR task.runtime_request_hash !~ '^[0-9a-f]{64}$' OR task.runtime_state_version<>1
 OR NULLIF(btrim(task.runtime_idempotency_key),'') IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_BINDING_INCOMPLETE' USING ERRCODE='42501';
 END IF;
 seed:=_agent_runtime_scheduled_profile_seed(task.id);
 result:=create_agent_runtime_scheduled_execution_profile_v1(
  task.id,task.runtime_action_id,(seed->>'source_run_id')::UUID,seed->'target',
  seed->>'toolset_hash',seed->>'canonical',0);
 IF result->>'outcome' NOT IN('created','already_exists') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_CREATE_FAILED' USING ERRCODE='42501';
 END IF;
END $$;
CREATE FUNCTION _create_agent_runtime_scheduled_profile_after_insert() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _ensure_agent_runtime_scheduled_profile(NEW.id);
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER create_runtime_scheduled_profile_after_insert
 AFTER INSERT ON scheduled_tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION _create_agent_runtime_scheduled_profile_after_insert();
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM scheduled_tasks task
  WHERE (task.runtime_action_id IS NOT NULL OR task.runtime_attempt_id IS NOT NULL
   OR task.runtime_request_hash IS NOT NULL OR task.runtime_idempotency_key IS NOT NULL)
  AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles profile
   WHERE profile.scheduled_task_id=task.id)) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_BACKFILL_REQUIRED' USING ERRCODE='55000';
 END IF;
END $$;
CREATE FUNCTION _agent_runtime_scheduled_gate_snapshot(
 p_org_id UUID,p_provider TEXT,p_capability TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g agent_runtime_tenant_gate_controls%ROWTYPE;te BIGINT:=0;pe BIGINT:=0;ce BIGINT:=0;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org_id||':tenant:tenant',0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
 te:=COALESCE(g.kill_epoch,0);
 IF FOUND AND(g.ingress_blocked OR g.claim_blocked OR g.dispatch_blocked) THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TENANT_FENCED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org_id||':provider:'||p_provider,0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org_id AND gate_scope='provider' AND scope_key=p_provider;
 pe:=COALESCE(g.kill_epoch,0); IF FOUND AND g.dispatch_blocked THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROVIDER_FENCED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||p_org_id||':capability:'||p_capability,0));
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls WHERE org_id=p_org_id AND gate_scope='capability' AND scope_key=p_capability;
 ce:=COALESCE(g.kill_epoch,0); IF FOUND AND g.dispatch_blocked THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_CAPABILITY_FENCED' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('tenant',te,'provider',pe,'capability',ce);
END $$;

CREATE FUNCTION _submit_agent_runtime_scheduled_execution_v1(
 p_task_id UUID,p_trigger_kind TEXT,p_trigger_key TEXT,p_scheduled_for TIMESTAMPTZ,
 p_manual_request_id TEXT,p_requester_user_id UUID,p_now TIMESTAMPTZ) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;e agent_runtime_scheduled_execution_profiles%ROWTYPE;
 b agent_runtime_scheduled_run_bindings%ROWTYPE;i agent_runtime_scheduled_submission_intents%ROWTYPE;
 q scheduled_task_runs%ROWTYPE;cat agent_runtime_catalog_facts%ROWTYPE;gate JSONB;
 conversation_id UUID:=gen_random_uuid();message_id UUID:=gen_random_uuid();
 session_id UUID:=gen_random_uuid();command_id UUID:=gen_random_uuid();
 key TEXT;context_hash TEXT;request_hash TEXT;receipt JSONB;config JSONB;capability JSONB;
 identity JSONB;envelope JSONB;payload JSONB;task_revision BIGINT;
BEGIN
 IF NOT _agent_runtime_scheduled_submission_enabled() THEN RETURN jsonb_build_object('outcome','runtime_disabled','owner_kind','runtime'); END IF;
 IF p_trigger_kind NOT IN('scheduled','manual') OR(p_trigger_kind='manual') IS DISTINCT FROM(p_manual_request_id IS NOT NULL)
 OR NULLIF(btrim(p_trigger_key),'') IS NULL OR p_requester_user_id IS NULL OR p_now IS NULL THEN RAISE EXCEPTION 'SCHEDULED_SUBMISSION_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-trigger-owner:'||p_task_id||':'||p_trigger_kind||':'||btrim(p_trigger_key),0));
 SELECT * INTO i FROM agent_runtime_scheduled_submission_intents WHERE scheduled_task_id=p_task_id AND trigger_kind=p_trigger_kind AND trigger_key=btrim(p_trigger_key);
 IF FOUND THEN SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=i.scheduled_run_id;
  RETURN jsonb_build_object('outcome','already_submitted','owner_kind','runtime','binding',to_jsonb(b),'command_id',i.command_id); END IF;
 SELECT * INTO t FROM scheduled_tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=p_task_id;
 IF t.id IS NULL OR e.scheduled_task_id IS NULL OR(t.org_id,t.user_id) IS DISTINCT FROM(e.org_id,e.user_id)
 OR t.status NOT IN('active','paused') OR(p_trigger_kind='scheduled' AND(t.status<>'active' OR t.next_run_at IS DISTINCT FROM p_scheduled_for OR t.next_run_at>p_now))
 OR NOT EXISTS(SELECT 1 FROM organizations WHERE id=t.org_id AND status='active')
 OR NOT EXISTS(SELECT 1 FROM org_members WHERE org_id=t.org_id AND user_id=t.user_id AND status='active') THEN
  RAISE EXCEPTION 'SCHEDULED_SUBMISSION_TASK_FENCED' USING ERRCODE='42501'; END IF;
 task_revision:=t.runtime_state_version; gate:=_agent_runtime_scheduled_gate_snapshot(t.org_id,e.provider_key,e.capability_key);
 SELECT * INTO cat FROM agent_runtime_catalog_facts WHERE catalog_revision=e.catalog_revision AND recoverable;
 IF cat.catalog_revision IS NULL THEN RAISE EXCEPTION 'SCHEDULED_SUBMISSION_CATALOG_MISSING' USING ERRCODE='42501'; END IF;
 context_hash:=encode(digest(convert_to(jsonb_build_object('task_id',t.id,'revision',task_revision,
  'prompt',t.prompt,'template_file',t.template_file,'last_summary',t.last_summary,'profile_version',e.state_version)::TEXT,'UTF8'),'sha256'),'hex');
 request_hash:=encode(digest(convert_to(jsonb_build_object('task_id',t.id,'trigger_kind',p_trigger_kind,
  'trigger_key',btrim(p_trigger_key),'context_hash',context_hash,'profile_hash',e.effective_toolset_hash)::TEXT,'UTF8'),'sha256'),'hex');
 UPDATE scheduled_tasks SET status='running',next_run_at=NULL,updated_at=p_now WHERE id=t.id;
 INSERT INTO scheduled_task_runs(task_id,org_id,status) VALUES(t.id,t.org_id,'running') RETURNING * INTO q;
 key:='scheduled-run:'||q.id;
 INSERT INTO conversations(id,user_id,org_id,source,scope_type,scope_id)
 VALUES(conversation_id,t.user_id,t.org_id,'scheduler','user',t.user_id::TEXT);
 INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,
  agent_definition_id,agent_definition_revision) VALUES(session_id,conversation_id,t.org_id,t.user_id,'system',
  'scheduler:'||t.id,t.user_id,e.agent_definition_id,e.agent_definition_revision);
 receipt:=jsonb_build_object('source','scheduler','scheduled_run_id',q.id,'context_hash',context_hash,
  'conversation_id',conversation_id,'session_id',session_id,'through_message_id',message_id,
  'base_context_revision','message:'||message_id);
 config:=jsonb_build_object('resolved_model',e.model_snapshot,'scheduled_budget',e.budget_snapshot,
  'release_revision','scheduled-runtime-disposable-v1','base_context_revision','message:'||message_id,
  'through_message_id',message_id);
 capability:=jsonb_build_object('channel',e.channel,'gate_state','disabled',
  'agent_definition_hash',e.agent_definition_hash,'tool_catalog_revision',e.catalog_revision,
  'tool_catalog_hash',cat.catalog_hash,'effective_toolset_revision',e.catalog_revision,
  'effective_toolset_hash',e.effective_toolset_hash);
 identity:=jsonb_build_object('source','scheduler','session_id',session_id,'idempotency_key',key,
  'scheduled_task_id',t.id,'scheduled_run_id',q.id,'trigger_kind',p_trigger_kind,'trigger_key',btrim(p_trigger_key),
  'request_hash',request_hash,'context_hash',context_hash,'tenant_kill_epoch',gate->>'tenant',
  'agent_definition_id',e.agent_definition_id,'agent_definition_revision',e.agent_definition_revision,
  'agent_definition_hash',e.agent_definition_hash,'catalog_revision',e.catalog_revision,
  'effective_toolset_hash',e.effective_toolset_hash);
 envelope:=jsonb_build_object('schema_revision',2,'run_kind','scheduled','request_identity',identity,
  'context_receipt',receipt,'config_snapshot',config,'capability_snapshot',capability);
 payload:=jsonb_build_object('text',t.prompt,'task_id',t.id,'scheduled_run_id',q.id,
  'template_ref',t.template_file,'previous_summary_ref',CASE WHEN t.last_summary IS NULL THEN NULL ELSE 'scheduled-task:'||t.id||':last-summary' END,
  'run_envelope',envelope,'release_revision','scheduled-runtime-disposable-v1');
 INSERT INTO agent_session_commands(id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash)
 VALUES(command_id,session_id,t.org_id,t.user_id,'submit_input',key,payload,
  md5(jsonb_build_object('command_type','submit_input','payload',payload)::TEXT));
 INSERT INTO messages(id,conversation_id,org_id,role,content)
 VALUES(message_id,conversation_id,t.org_id,'user',
  jsonb_build_array(jsonb_build_object('type','text','text',t.prompt))::TEXT);
 INSERT INTO agent_runtime_scheduled_run_bindings(scheduled_run_id,scheduled_task_id,org_id,user_id,owner_kind,
  trigger_kind,trigger_key,scheduled_for,manual_request_id,task_revision,task_status,profile_state_version,
  context_hash,request_hash,tenant_kill_epoch,provider_kill_epoch,capability_kill_epoch,provider_revision,
  capability_revision,runtime_command_id,owner_status,state_version)
 VALUES(q.id,t.id,t.org_id,t.user_id,'runtime',p_trigger_kind,btrim(p_trigger_key),p_scheduled_for,p_manual_request_id,
  task_revision,'running',e.state_version,context_hash,request_hash,(gate->>'tenant')::BIGINT,
  (gate->>'provider')::BIGINT,(gate->>'capability')::BIGINT,e.provider_revision,e.capability_revision,
  command_id,'submitted',1) RETURNING * INTO b;
 INSERT INTO agent_runtime_scheduled_submission_intents(
  scheduled_run_id,scheduled_task_id,org_id,user_id,requester_user_id,trigger_kind,
  trigger_key,scheduled_for,manual_request_id,conversation_id,message_id,session_id,command_id,
  request_hash,context_hash,task_revision,profile_state_version,created_at)
 VALUES(q.id,t.id,t.org_id,t.user_id,p_requester_user_id,p_trigger_kind,
  btrim(p_trigger_key),p_scheduled_for,p_manual_request_id,conversation_id,message_id,session_id,command_id,
  request_hash,context_hash,task_revision,e.state_version,clock_timestamp());
 PERFORM append_agent_runtime_event(session_id,'command.accepted',NULL,NULL,command_id,'system','scheduler',
  jsonb_build_object('command_id',command_id,'scheduled_run_id',q.id),ARRAY['audit']::TEXT[]);
 RETURN jsonb_build_object('outcome','submitted','owner_kind','runtime','binding',to_jsonb(b),'command_id',command_id);
END $$;

CREATE FUNCTION worker_claim_due_scheduled_executions_v1(p_now TIMESTAMPTZ,p_limit INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;items JSONB:='[]'::JSONB;item JSONB;claimed INTEGER:=0;
BEGIN
 PERFORM _agent_runtime_scheduled_submission_worker();
 IF p_now IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'SCHEDULED_WORKER_CLAIM_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 FOR t IN SELECT candidate.* FROM scheduled_tasks candidate WHERE candidate.status='active'
  AND candidate.next_run_at IS NOT NULL AND candidate.next_run_at<=p_now
  AND(_agent_runtime_scheduled_submission_enabled() OR NOT EXISTS(
   SELECT 1 FROM agent_runtime_scheduled_execution_profiles profile
   WHERE profile.scheduled_task_id=candidate.id))
  AND(candidate.org_id IS NULL OR EXISTS(SELECT 1 FROM organizations o WHERE o.id=candidate.org_id AND o.status='active'))
  ORDER BY candidate.next_run_at,candidate.id LIMIT p_limit*4 FOR UPDATE OF candidate SKIP LOCKED LOOP
  EXIT WHEN claimed>=p_limit;
  IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles e WHERE e.scheduled_task_id=t.id) THEN
   item:=_submit_agent_runtime_scheduled_execution_v1(t.id,'scheduled',
    'scheduled:'||t.next_run_at::TEXT,t.next_run_at,NULL,t.user_id,p_now);
   IF item->>'outcome'='runtime_disabled' THEN CONTINUE; END IF;
  ELSE
   UPDATE scheduled_tasks SET status='running',next_run_at=NULL,updated_at=p_now WHERE id=t.id RETURNING * INTO t;
   item:=jsonb_build_object('outcome','claimed','owner_kind','legacy','task',to_jsonb(t));
  END IF;
  items:=items||jsonb_build_array(item);claimed:=claimed+1;
 END LOOP; RETURN items;
END $$;

CREATE FUNCTION worker_assert_scheduled_task_legacy_owner_v1(p_task_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_submission_worker();
 IF p_task_id IS NULL THEN RAISE EXCEPTION 'SCHEDULED_LEGACY_OWNER_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=p_task_id) THEN
  RAISE EXCEPTION 'SCHEDULED_RUN_RUNTIME_OWNED' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('outcome','allowed','owner_kind','legacy');
END $$;

CREATE FUNCTION request_agent_runtime_scheduled_execution_v1(
 p_request_id TEXT,p_task_id UUID,p_org_id UUID,p_user_id UUID,p_expected_task_version BIGINT,p_now TIMESTAMPTZ) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;request_id TEXT:=btrim(COALESCE(p_request_id,''));
 i agent_runtime_scheduled_submission_intents%ROWTYPE;b agent_runtime_scheduled_run_bindings%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 IF length(request_id) NOT BETWEEN 1 AND 128 OR tenant_org_id() IS DISTINCT FROM p_org_id
 OR tenant_actor_user_id() IS DISTINCT FROM p_user_id THEN RAISE EXCEPTION 'SCHEDULED_MANUAL_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'scheduled-manual-request:'||p_org_id||':'||p_user_id||':'||request_id,0));
 SELECT * INTO i FROM agent_runtime_scheduled_submission_intents
  WHERE org_id=p_org_id AND requester_user_id=p_user_id AND trigger_kind='manual'
   AND manual_request_id=request_id;
 IF FOUND THEN
  IF i.scheduled_task_id IS DISTINCT FROM p_task_id THEN
   RAISE EXCEPTION 'SCHEDULED_MANUAL_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001';
  END IF;
  SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=i.scheduled_run_id;
  RETURN jsonb_build_object('outcome','already_submitted','owner_kind','runtime',
   'binding',to_jsonb(b),'command_id',i.command_id);
 END IF;
 SELECT * INTO t FROM scheduled_tasks WHERE id=p_task_id FOR SHARE;
 IF t.id IS NULL OR t.org_id IS DISTINCT FROM p_org_id OR t.runtime_state_version IS DISTINCT FROM p_expected_task_version
 OR NOT _runtime_scheduler_operation_allowed(p_org_id,p_user_id,'update',t.user_id) THEN RAISE EXCEPTION 'SCHEDULED_MANUAL_TASK_FENCED' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=t.id) THEN
  RETURN jsonb_build_object('outcome','legacy_owner','owner_kind','legacy'); END IF;
 RETURN _submit_agent_runtime_scheduled_execution_v1(t.id,'manual',
  'manual:'||p_user_id||':'||request_id,NULL,request_id,p_user_id,p_now);
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_submission_v1(p_task_id UUID,p_trigger_kind TEXT,p_trigger_key TEXT) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE i agent_runtime_scheduled_submission_intents%ROWTYPE;b agent_runtime_scheduled_run_bindings%ROWTYPE;
BEGIN
 IF session_user='everydayai_worker' THEN PERFORM _agent_runtime_scheduled_submission_worker();
 ELSE PERFORM _assert_agent_runtime_actor(FALSE); END IF;
 SELECT * INTO i FROM agent_runtime_scheduled_submission_intents WHERE scheduled_task_id=p_task_id AND trigger_kind=p_trigger_kind AND trigger_key=btrim(p_trigger_key);
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF session_user<>'everydayai_worker' AND(tenant_org_id() IS DISTINCT FROM i.org_id
 OR tenant_actor_user_id()<>ALL(ARRAY[i.user_id,i.requester_user_id])) THEN
  RAISE EXCEPTION 'SCHEDULED_SUBMISSION_TENANT_MISMATCH' USING ERRCODE='42501'; END IF;
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=i.scheduled_run_id;
 RETURN jsonb_build_object('outcome','found','owner_kind','runtime','binding',to_jsonb(b),'command_id',i.command_id);
END $$;

CREATE OR REPLACE FUNCTION _agent_command_run_envelope(p_command agent_session_commands) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e JSONB:=p_command.payload->'run_envelope';s agent_runtime_sessions%ROWTYPE;
BEGIN
 IF jsonb_typeof(e) IS DISTINCT FROM 'object' OR e='{}'::JSONB
 OR e->>'run_kind' NOT IN('user','continuation','scheduled')
 OR jsonb_typeof(e->'context_receipt') IS DISTINCT FROM 'object'
 OR jsonb_typeof(e->'config_snapshot') IS DISTINCT FROM 'object'
 OR jsonb_typeof(e->'capability_snapshot') IS DISTINCT FROM 'object'
 OR jsonb_typeof(e->'request_identity') IS DISTINCT FROM 'object'
 OR e->'context_receipt'='{}'::JSONB OR e->'config_snapshot'='{}'::JSONB OR e->'capability_snapshot'='{}'::JSONB
 OR e->'request_identity'->>'session_id' IS DISTINCT FROM p_command.session_id::TEXT
 OR e->'request_identity'->>'idempotency_key' IS DISTINCT FROM p_command.idempotency_key
 OR pg_column_size(e)>262144 THEN RETURN NULL; END IF;
 IF e->>'run_kind'='scheduled' THEN
  SELECT * INTO s FROM agent_runtime_sessions WHERE id=p_command.session_id;
  IF s.scope_kind IS DISTINCT FROM 'system' OR NOT EXISTS(
   SELECT 1 FROM agent_runtime_scheduled_submission_intents i
   JOIN agent_runtime_scheduled_run_bindings b ON b.scheduled_run_id=i.scheduled_run_id
   JOIN agent_runtime_scheduled_execution_profiles p ON p.scheduled_task_id=i.scheduled_task_id
   WHERE i.command_id=p_command.id AND i.session_id=s.id AND b.runtime_command_id=p_command.id
    AND b.owner_kind='runtime' AND b.owner_status='submitted' AND b.request_hash=i.request_hash
    AND e->'request_identity'->>'scheduled_run_id'=i.scheduled_run_id::TEXT
    AND e->'request_identity'->>'request_hash'=i.request_hash
    AND e->'request_identity'->>'effective_toolset_hash'=p.effective_toolset_hash
  ) THEN RETURN NULL; END IF;
 END IF; RETURN e;
END $$;

CREATE FUNCTION _bind_agent_runtime_scheduled_run_after_claim() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE b agent_runtime_scheduled_run_bindings%ROWTYPE;r agent_runs%ROWTYPE;e JSONB;
BEGIN
 IF NEW.result_entity_id IS NULL OR OLD.result_entity_id IS NOT NULL THEN RETURN NEW; END IF;
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE runtime_command_id=NEW.id FOR UPDATE;
 IF NOT FOUND THEN RETURN NEW; END IF;
 SELECT * INTO r FROM agent_runs WHERE id=NEW.result_entity_id AND command_id=NEW.id;
 e:=_agent_command_run_envelope(NEW);
 IF r.id IS NULL OR r.run_kind<>'scheduled' OR e IS NULL OR r.request_hash IS DISTINCT FROM
  _agent_run_request_hash(NEW.id,'scheduled',e->'context_receipt',e->'config_snapshot',e->'capability_snapshot') THEN
  RAISE EXCEPTION 'SCHEDULED_RUNTIME_RUN_BINDING_INVALID' USING ERRCODE='42501'; END IF;
 UPDATE agent_runtime_scheduled_run_bindings SET runtime_run_id=r.id,owner_status='runtime_claimed',
  state_version=state_version+1,updated_at=clock_timestamp() WHERE scheduled_run_id=b.scheduled_run_id;
 RETURN NEW;
END $$;
CREATE TRIGGER bind_runtime_scheduled_run_after_claim AFTER UPDATE OF result_entity_id ON agent_session_commands
 FOR EACH ROW EXECUTE FUNCTION _bind_agent_runtime_scheduled_run_after_claim();

CREATE OR REPLACE FUNCTION worker_claim_due_scheduled_tasks(p_now TIMESTAMPTZ,p_limit INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v_tasks JSONB;
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_now IS NULL OR p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN RAISE EXCEPTION 'SCHEDULED_WORKER_CLAIM_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 WITH claimed AS(UPDATE scheduled_tasks task SET status='running',next_run_at=NULL,updated_at=p_now
  WHERE task.id IN(SELECT candidate.id FROM scheduled_tasks candidate WHERE candidate.status='active'
   AND candidate.next_run_at IS NOT NULL AND candidate.next_run_at<=p_now
   AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles p WHERE p.scheduled_task_id=candidate.id)
   AND(candidate.org_id IS NULL OR EXISTS(SELECT 1 FROM organizations o WHERE o.id=candidate.org_id AND o.status='active'))
   ORDER BY candidate.next_run_at LIMIT p_limit FOR UPDATE OF candidate SKIP LOCKED) RETURNING task.*)
 SELECT COALESCE(jsonb_agg(to_jsonb(claimed)),'[]'::JSONB) INTO v_tasks FROM claimed; RETURN v_tasks;
END $$;

CREATE OR REPLACE FUNCTION worker_create_scheduled_run(p_task_id UUID,p_lease_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;q scheduled_task_runs%ROWTYPE;token UUID:=gen_random_uuid();
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_task_id IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN RAISE EXCEPTION 'SCHEDULED_RUN_CREATE_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO t FROM scheduled_tasks WHERE id=p_task_id AND status='running' FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_running'); END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=t.id) THEN
  RAISE EXCEPTION 'SCHEDULED_RUN_RUNTIME_OWNED' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM scheduled_task_runs WHERE task_id=t.id AND status='running') THEN RETURN jsonb_build_object('outcome','already_running'); END IF;
 INSERT INTO scheduled_task_runs(task_id,org_id,status,execution_token,lease_expires_at)
 VALUES(t.id,t.org_id,'running',token,clock_timestamp()+make_interval(secs=>p_lease_seconds)) RETURNING * INTO q;
 RETURN jsonb_build_object('outcome','created','run',to_jsonb(q)-'execution_token','execution_token',token);
END $$;

CREATE OR REPLACE FUNCTION worker_list_stale_scheduled_tasks(p_cutoff TIMESTAMPTZ) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE tasks JSONB;
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_cutoff IS NULL THEN RAISE EXCEPTION 'SCHEDULED_WORKER_STALE_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(task)),'[]'::JSONB) INTO tasks FROM scheduled_tasks task
 WHERE task.status='running' AND task.updated_at<p_cutoff
 AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles p WHERE p.scheduled_task_id=task.id);
 RETURN tasks;
END $$;
CREATE OR REPLACE FUNCTION worker_recover_stale_scheduled_task(
 p_task_id UUID,p_cutoff TIMESTAMPTZ,p_status TEXT,p_next_run_at TIMESTAMPTZ,p_now TIMESTAMPTZ) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_task_id IS NULL OR p_cutoff IS NULL OR p_now IS NULL OR p_status NOT IN('active','paused') THEN
  RAISE EXCEPTION 'SCHEDULED_WORKER_RECOVER_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=p_task_id) THEN
  RETURN jsonb_build_object('outcome','runtime_owned'); END IF;
 UPDATE scheduled_tasks SET status=p_status,next_run_at=p_next_run_at,updated_at=p_now
 WHERE id=p_task_id AND status='running' AND updated_at<p_cutoff;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_recovered'); END IF;
 UPDATE scheduled_task_runs SET status='failed',error_message='进程异常退出，任务自动恢复',finished_at=p_now
 WHERE task_id=p_task_id AND status='running'; RETURN jsonb_build_object('outcome','recovered');
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_submission_worker(),
 _agent_runtime_scheduled_submission_enabled(),_agent_runtime_scheduled_profile_seed(UUID),
 _ensure_agent_runtime_scheduled_profile(UUID),_create_agent_runtime_scheduled_profile_after_insert(),
 _agent_runtime_scheduled_gate_snapshot(UUID,TEXT,TEXT),
 _submit_agent_runtime_scheduled_execution_v1(UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,UUID,TIMESTAMPTZ),
 _bind_agent_runtime_scheduled_run_after_claim(),worker_claim_due_scheduled_executions_v1(TIMESTAMPTZ,INTEGER),
 worker_assert_scheduled_task_legacy_owner_v1(UUID),
 request_agent_runtime_scheduled_execution_v1(TEXT,UUID,UUID,UUID,BIGINT,TIMESTAMPTZ),
 read_agent_runtime_scheduled_submission_v1(UUID,TEXT,TEXT) FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION worker_claim_due_scheduled_executions_v1(TIMESTAMPTZ,INTEGER),
 worker_assert_scheduled_task_legacy_owner_v1(UUID),
 read_agent_runtime_scheduled_submission_v1(UUID,TEXT,TEXT) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION request_agent_runtime_scheduled_execution_v1(TEXT,UUID,UUID,UUID,BIGINT,TIMESTAMPTZ),
 read_agent_runtime_scheduled_submission_v1(UUID,TEXT,TEXT) TO everydayai_runtime;
RESET ROLE;
