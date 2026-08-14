-- AR-17.4 additive lane. 224-226 remain immutable.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_rollout_subjects (
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('organization','user')),
    subject_id TEXT NOT NULL CHECK (length(btrim(subject_id)) BETWEEN 1 AND 200),
    channel TEXT NOT NULL CHECK (channel IN ('web','wecom')),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    capabilities JSONB NOT NULL DEFAULT '[]'::JSONB
      CHECK (jsonb_typeof(capabilities)='array'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(subject_kind,subject_id,channel)
);
ALTER TABLE agent_runtime_rollout_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_rollout_subjects FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_rollout_subjects_owner_all ON agent_runtime_rollout_subjects
 FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON agent_runtime_rollout_subjects FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai;

CREATE TABLE agent_runtime_production_bindings (
    catalog_revision TEXT NOT NULL REFERENCES agent_runtime_catalog_facts(catalog_revision),
    tool_name TEXT NOT NULL,
    provider_revision TEXT NOT NULL,
    secret_binding TEXT,
    readiness_hash TEXT NOT NULL CHECK (readiness_hash ~ '^[0-9a-f]{64}$'),
    ready BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(catalog_revision,tool_name)
);
ALTER TABLE agent_runtime_production_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_production_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_production_bindings_owner_all ON agent_runtime_production_bindings
 FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON agent_runtime_production_bindings FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai;

CREATE TABLE agent_runtime_shadow_mismatches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    command_id UUID,
    run_id UUID,
    category TEXT NOT NULL,
    expected_value TEXT NOT NULL,
    actual_value TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(details)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE agent_runtime_shadow_mismatches ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_shadow_mismatches FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_shadow_mismatches_owner_all ON agent_runtime_shadow_mismatches
 FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON agent_runtime_shadow_mismatches FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai;

CREATE FUNCTION set_agent_runtime_rollout_subject(
 p_subject_kind TEXT,p_subject_id TEXT,p_channel TEXT,p_enabled BOOLEAN,p_capabilities JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_runtime_admin'
    OR current_setting('app.access_kind',true)<>'runtime_admin'
    OR NOT tenant_platform_admin() THEN RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501'; END IF;
 IF p_subject_kind NOT IN ('organization','user') OR p_channel NOT IN ('web','wecom')
    OR NULLIF(btrim(p_subject_id),'') IS NULL OR jsonb_typeof(p_capabilities) IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'RUNTIME_ROLLOUT_SUBJECT_INVALID' USING ERRCODE='22023';
 END IF;
 INSERT INTO agent_runtime_rollout_subjects(subject_kind,subject_id,channel,enabled,capabilities,updated_at)
 VALUES(p_subject_kind,btrim(p_subject_id),p_channel,p_enabled,p_capabilities,clock_timestamp())
 ON CONFLICT(subject_kind,subject_id,channel) DO UPDATE SET enabled=EXCLUDED.enabled,
 capabilities=EXCLUDED.capabilities,updated_at=clock_timestamp();
 RETURN jsonb_build_object('outcome','applied','enabled',p_enabled);
END $$;

CREATE FUNCTION record_agent_runtime_shadow_mismatch(
 p_command_id UUID,p_run_id UUID,p_category TEXT,p_expected TEXT,p_actual TEXT,p_details JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF NULLIF(btrim(p_category),'') IS NULL OR jsonb_typeof(p_details) IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'RUNTIME_SHADOW_MISMATCH_INVALID' USING ERRCODE='22023';
 END IF;
 INSERT INTO agent_runtime_shadow_mismatches(command_id,run_id,category,expected_value,actual_value,details)
 VALUES(p_command_id,p_run_id,btrim(p_category),COALESCE(p_expected,''),COALESCE(p_actual,''),p_details);
 RETURN jsonb_build_object('outcome','recorded');
END $$;

CREATE FUNCTION runtime_submit_ingress_v3(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,
 p_created_by_user_id UUID,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_agent_definition_hash TEXT,p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,
 p_through_message_id UUID,p_base_context_revision TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_config_snapshot JSONB,p_capability_snapshot JSONB,
 p_release_revision TEXT,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ctl agent_runtime_control%ROWTYPE; s JSONB; sid UUID; d agent_runtime_definition_facts%ROWTYPE;
 c agent_runtime_catalog_facts%ROWTYPE; t agent_runtime_effective_toolset_facts%ROWTYPE;
 v_gate TEXT; command_result JSONB; config JSONB; capabilities JSONB; rollout_ok BOOLEAN;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 IF p_scope_kind NOT IN ('user','channel') OR p_channel NOT IN ('web','wecom')
    OR p_through_message_id IS NULL OR p_base_context_revision IS DISTINCT FROM 'message:'||p_through_message_id::TEXT
    OR NULLIF(btrim(p_scope_id),'') IS NULL OR NULLIF(btrim(p_idempotency_key),'') IS NULL
    OR p_command_type IS DISTINCT FROM 'submit_input'
    OR jsonb_typeof(COALESCE(p_payload,'{}'::JSONB)) IS DISTINCT FROM 'object' THEN
   RAISE EXCEPTION 'RUNTIME_INGRESS_V3_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT r.enabled AND EXISTS(
   SELECT 1 FROM jsonb_array_elements(r.capabilities) x WHERE x #>>'{}'='runtime_ingress'
 ) INTO rollout_ok FROM agent_runtime_rollout_subjects r
 WHERE r.channel=p_channel AND r.enabled AND (
   (r.subject_kind='user' AND r.subject_id=p_user_id::TEXT) OR
   (p_org_id IS NOT NULL AND r.subject_kind='organization' AND r.subject_id=p_org_id::TEXT)
 ) LIMIT 1;
 IF NOT COALESCE(rollout_ok,FALSE) THEN RETURN jsonb_build_object('outcome','subject_not_enabled','ingress_version',3); END IF;
 SELECT * INTO ctl FROM agent_runtime_control WHERE singleton FOR UPDATE;
 IF NOT ctl.ingress_enabled THEN RETURN jsonb_build_object('outcome','ingress_disabled','ingress_version',3); END IF;
 IF NOT EXISTS (SELECT 1 FROM messages m WHERE m.id=p_through_message_id AND m.conversation_id=p_conversation_id
   AND m.org_id IS NOT DISTINCT FROM p_org_id) THEN RAISE EXCEPTION 'RUNTIME_CONTEXT_ANCHOR_MISSING'; END IF;
 s:=ensure_agent_runtime_session(p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,p_created_by_user_id,
   p_agent_definition_id,p_agent_definition_revision);
 IF s->>'outcome' NOT IN ('created','already_exists') THEN RETURN s; END IF;
 sid:=(s->>'entity_id')::UUID;
 SELECT * INTO d FROM agent_runtime_definition_facts WHERE agent_key=p_agent_definition_id
   AND definition_revision=p_agent_definition_revision AND recoverable AND enabled_for_new_ingress;
 SELECT * INTO c FROM agent_runtime_catalog_facts WHERE catalog_revision=d.catalog_revision AND recoverable AND enabled_for_new_ingress;
 IF d.agent_key IS NULL OR c.catalog_revision IS NULL OR d.definition_hash IS DISTINCT FROM p_agent_definition_hash
    OR p_effective_toolset_revision IS DISTINCT FROM d.catalog_revision THEN RAISE EXCEPTION 'RUNTIME_VERSION_FACT_NOT_ENABLED'; END IF;
 IF (SELECT count(*) FROM agent_runtime_production_bindings b WHERE b.catalog_revision=c.catalog_revision AND b.ready)
    <> COALESCE(jsonb_array_length(c.catalog_document->'tools'),-1)
    OR EXISTS (SELECT 1 FROM jsonb_array_elements(c.catalog_document->'tools') tool
      WHERE COALESCE(tool->>'safety_level','safe')<>'safe'
      AND NOT EXISTS (SELECT 1 FROM agent_runtime_production_bindings b
        WHERE b.catalog_revision=c.catalog_revision AND b.tool_name=tool->>'canonical_name'
          AND b.ready AND NULLIF(btrim(b.secret_binding),'') IS NOT NULL)) THEN
   RETURN jsonb_build_object('outcome','production_not_ready','ingress_version',3);
 END IF;
 v_gate:='disabled';
 IF ctl.non_safe_actions_enabled AND ctl.code_execute_enabled AND ctl.tool_confirmation_enabled
    AND EXISTS(SELECT 1 FROM agent_runtime_worker_heartbeats h WHERE h.process_role='sandbox' AND h.ready AND NOT h.draining
      AND h.observed_at>clock_timestamp()-interval '30 seconds') THEN v_gate:='enabled'; END IF;
 SELECT * INTO t FROM agent_runtime_effective_toolset_facts WHERE agent_key=d.agent_key AND definition_revision=d.definition_revision
   AND catalog_revision=d.catalog_revision AND scope_kind=p_scope_kind AND channel=p_channel AND gate_state=v_gate
   AND recoverable AND enabled_for_new_ingress;
 IF t.effective_toolset_hash IS NULL THEN RAISE EXCEPTION 'RUNTIME_EFFECTIVE_TOOLSET_FACT_MISSING'; END IF;
 config:=COALESCE(p_config_snapshot,'{}'::JSONB)||jsonb_build_object('base_context_revision',p_base_context_revision,
   'through_message_id',p_through_message_id,'agent_definition_revision',p_agent_definition_revision,
   'agent_definition_hash',p_agent_definition_hash,'tool_catalog_revision',d.catalog_revision,'tool_catalog_hash',c.catalog_hash,
   'effective_toolset_revision',d.catalog_revision,'effective_toolset_hash',t.effective_toolset_hash,
   'release_revision',p_release_revision,'config_snapshot_hash',md5(COALESCE(p_config_snapshot,'{}'::JSONB)::TEXT));
 capabilities:=COALESCE(p_capability_snapshot,'{}'::JSONB)||jsonb_build_object('channel',p_channel,
   'agent_definition_id',p_agent_definition_id,'agent_definition_revision',p_agent_definition_revision,
   'agent_definition_hash',p_agent_definition_hash,'tool_catalog_revision',d.catalog_revision,'tool_catalog_hash',c.catalog_hash,
   'effective_toolset_revision',d.catalog_revision,'effective_toolset_hash',t.effective_toolset_hash,'gate_state',v_gate,
   'capability_snapshot_hash',md5(COALESCE(p_capability_snapshot,'{}'::JSONB)::TEXT));
 command_result:=submit_session_command(sid,p_command_type,p_idempotency_key,COALESCE(p_payload,'{}'::JSONB)||jsonb_build_object(
   'run_envelope',jsonb_build_object('schema_revision',3,'run_kind','user','context_receipt',jsonb_build_object(
     'base_context_revision',p_base_context_revision,'through_message_id',p_through_message_id,'session_id',sid,'conversation_id',p_conversation_id),
     'config_snapshot',config,'capability_snapshot',capabilities,'request_identity',jsonb_build_object(
     'session_id',sid,'idempotency_key',p_idempotency_key,'channel',p_channel,'conversation_id',p_conversation_id,
     'user_id',p_user_id,'org_id',p_org_id,'scope_kind',p_scope_kind,'scope_id',p_scope_id,'through_message_id',p_through_message_id,
     'base_context_revision',p_base_context_revision,'agent_definition_id',p_agent_definition_id,
     'agent_definition_revision',p_agent_definition_revision,'agent_definition_hash',p_agent_definition_hash,
     'catalog_revision',d.catalog_revision,'effective_toolset_hash',t.effective_toolset_hash)),
   'release_revision',p_release_revision));
 RETURN command_result||jsonb_build_object('session_id',sid,'ingress_version',3,'effective_toolset_revision',d.catalog_revision,
   'effective_toolset_hash',t.effective_toolset_hash,'gate_state',v_gate);
END $$;

REVOKE ALL ON FUNCTION set_agent_runtime_rollout_subject(TEXT,TEXT,TEXT,BOOLEAN,JSONB),
 record_agent_runtime_shadow_mismatch(UUID,UUID,TEXT,TEXT,TEXT,JSONB),
 runtime_submit_ingress_v3(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB)
 FROM PUBLIC;
GRANT EXECUTE ON FUNCTION set_agent_runtime_rollout_subject(TEXT,TEXT,TEXT,BOOLEAN,JSONB) TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION record_agent_runtime_shadow_mismatch(UUID,UUID,TEXT,TEXT,TEXT,JSONB) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v3(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB)
 TO everydayai_runtime,everydayai_wecom_runtime;

CREATE FUNCTION enqueue_wecom_runtime_turn_v5(
 p_task_data JSONB,p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,
 p_input_content JSONB,p_delivery_context JSONB,p_agent_definition_id TEXT,
 p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_release_revision TEXT,p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e JSONB; r JSONB; conversation_id UUID; user_id UUID; org_id UUID; scope_kind TEXT; scope_id TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 e:=enqueue_wecom_generation_turn_v2(p_task_data,p_input_message_id,p_output_message_id,p_turn_id,p_input_content,p_delivery_context);
 SELECT t.conversation_id,t.user_id,t.org_id INTO conversation_id,user_id,org_id FROM tasks t WHERE t.id=(e->>'task_id')::UUID FOR UPDATE;
 SELECT c.scope_type,c.scope_id INTO scope_kind,scope_id FROM conversations c WHERE c.id=conversation_id;
 r:=runtime_submit_ingress_v3(conversation_id,org_id,user_id,scope_kind,scope_id,user_id,p_agent_definition_id,p_agent_definition_revision,
   p_agent_definition_hash,'submit_input',p_idempotency_key,'wecom',p_input_message_id,'message:'||p_input_message_id,
   p_effective_toolset_revision,p_effective_toolset_hash,'{}'::JSONB,jsonb_build_object('requested_groups',jsonb_build_array('code')),
   p_release_revision,jsonb_build_object('schema_revision',3,'channel','wecom','task_id',e->>'task_id','input_message_id',p_input_message_id,
   'output_message_id',p_output_message_id,'turn_id',p_turn_id,'content',p_input_content,'delivery_context',p_delivery_context||'{"actor":false,"runtime":true}'::JSONB));
 IF r->>'outcome' IN ('ingress_disabled','subject_not_enabled') THEN RETURN e||jsonb_build_object('runtime_owned',false); END IF;
 IF r->>'outcome' NOT IN ('created','already_exists') THEN RAISE EXCEPTION 'WECOM_RUNTIME_INGRESS_V5_FAILED: %',r->>'outcome' USING ERRCODE='55000'; END IF;
 UPDATE tasks SET delivery_context=p_delivery_context||'{"actor":false,"runtime":true}'::JSONB WHERE id=(e->>'task_id')::UUID;
 RETURN e||jsonb_build_object('runtime_owned',true,'runtime_session_id',r->>'session_id','runtime_command_id',r->>'entity_id',
   'effective_toolset_revision',r->>'effective_toolset_revision','effective_toolset_hash',r->>'effective_toolset_hash','gate_state',r->>'gate_state');
END $$;
REVOKE ALL ON FUNCTION enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT) TO everydayai_wecom_runtime;
RESET ROLE;
