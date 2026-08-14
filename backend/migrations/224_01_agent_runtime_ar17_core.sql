SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_runtime_definition_facts (
    agent_key TEXT NOT NULL,
    definition_revision TEXT NOT NULL,
    definition_hash TEXT NOT NULL CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
    prompt_revision TEXT NOT NULL,
    catalog_revision TEXT NOT NULL CHECK (catalog_revision ~ '^[0-9a-f]{64}$'),
    effective_toolset_hash TEXT NOT NULL CHECK (effective_toolset_hash ~ '^[0-9a-f]{64}$'),
    definition_document JSONB NOT NULL DEFAULT '{}'::JSONB
      CHECK (jsonb_typeof(definition_document)='object'),
    enabled_for_new_ingress BOOLEAN NOT NULL DEFAULT TRUE,
    recoverable BOOLEAN NOT NULL DEFAULT TRUE,
    used_by_ingress BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (agent_key, definition_revision),
    CHECK (length(btrim(agent_key)) BETWEEN 1 AND 200),
    CHECK (length(btrim(definition_revision)) BETWEEN 1 AND 200),
    CHECK (length(btrim(prompt_revision)) BETWEEN 1 AND 200)
);
ALTER TABLE agent_runtime_definition_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_definition_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_definition_facts_owner_all
 ON agent_runtime_definition_facts FOR ALL TO everydayai_owner
 USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON TABLE agent_runtime_definition_facts
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai;
CREATE TABLE agent_runtime_catalog_facts (
    catalog_revision TEXT PRIMARY KEY CHECK (catalog_revision ~ '^[0-9a-f]{64}$'),
    catalog_hash TEXT NOT NULL CHECK (catalog_hash ~ '^[0-9a-f]{64}$'),
    catalog_document JSONB NOT NULL CHECK (jsonb_typeof(catalog_document)='object'),
    enabled_for_new_ingress BOOLEAN NOT NULL DEFAULT TRUE,
    recoverable BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE agent_runtime_effective_toolset_facts (
    agent_key TEXT NOT NULL,
    definition_revision TEXT NOT NULL,
    catalog_revision TEXT NOT NULL REFERENCES agent_runtime_catalog_facts(catalog_revision),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user','channel')),
    channel TEXT NOT NULL CHECK (channel IN ('web','wecom')),
    gate_state TEXT NOT NULL CHECK (gate_state IN ('enabled','disabled')),
    effective_toolset_hash TEXT NOT NULL CHECK (effective_toolset_hash ~ '^[0-9a-f]{64}$'),
    toolset_document JSONB NOT NULL CHECK (jsonb_typeof(toolset_document)='object'),
    enabled_for_new_ingress BOOLEAN NOT NULL DEFAULT TRUE,
    recoverable BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (agent_key,definition_revision,catalog_revision,scope_kind,channel,gate_state)
);
ALTER TABLE agent_runtime_catalog_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_catalog_facts FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_effective_toolset_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_effective_toolset_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_catalog_facts_owner_all ON agent_runtime_catalog_facts
 FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_toolset_facts_owner_all ON agent_runtime_effective_toolset_facts
 FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON TABLE agent_runtime_catalog_facts,agent_runtime_effective_toolset_facts
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai;
CREATE FUNCTION get_agent_runtime_version_facts(
 p_agent_key TEXT,p_definition_revision TEXT,p_catalog_revision TEXT,
 p_scope_kind TEXT,p_channel TEXT,p_effective_toolset_hash TEXT
) RETURNS JSONB LANGUAGE SQL STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
 SELECT jsonb_build_object(
   'definition_fact',(SELECT to_jsonb(d) FROM agent_runtime_definition_facts d
     WHERE d.agent_key=p_agent_key AND d.definition_revision=p_definition_revision
       AND d.recoverable),
   'catalog_fact',(SELECT to_jsonb(c) FROM agent_runtime_catalog_facts c
     WHERE c.catalog_revision=p_catalog_revision AND c.recoverable),
   'effective_toolset_fact',(SELECT to_jsonb(e) FROM agent_runtime_effective_toolset_facts e
     WHERE e.agent_key=p_agent_key AND e.definition_revision=p_definition_revision
       AND e.catalog_revision=p_catalog_revision AND e.scope_kind=p_scope_kind
       AND e.channel=p_channel AND e.effective_toolset_hash=p_effective_toolset_hash
       AND e.recoverable)
 )
$$;
CREATE FUNCTION set_agent_runtime_definition_ingress_enabled(
 p_agent_key TEXT,p_definition_revision TEXT,p_enabled BOOLEAN
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
 IF session_user<>'everydayai_runtime_admin'
    OR current_setting('app.access_kind',true)<>'runtime_admin'
    OR NOT tenant_platform_admin() THEN
   RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 UPDATE agent_runtime_definition_facts
  SET enabled_for_new_ingress=p_enabled
  WHERE agent_key=p_agent_key AND definition_revision=p_definition_revision;
 IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_RUNTIME_DEFINITION_FACT_MISSING'; END IF;
 IF p_enabled THEN
   UPDATE agent_runtime_catalog_facts c
    SET enabled_for_new_ingress=TRUE
    FROM agent_runtime_definition_facts d
    WHERE d.agent_key=p_agent_key AND d.definition_revision=p_definition_revision
      AND c.catalog_revision=d.catalog_revision;
 ELSE
   UPDATE agent_runtime_catalog_facts c
    SET enabled_for_new_ingress=FALSE
    FROM agent_runtime_definition_facts d
    WHERE d.agent_key=p_agent_key AND d.definition_revision=p_definition_revision
      AND c.catalog_revision=d.catalog_revision
      AND NOT EXISTS (SELECT 1 FROM agent_runtime_definition_facts other
       WHERE other.catalog_revision=c.catalog_revision
         AND other.enabled_for_new_ingress
         AND (other.agent_key,other.definition_revision)
             IS DISTINCT FROM (p_agent_key,p_definition_revision));
 END IF;
 UPDATE agent_runtime_effective_toolset_facts e
  SET enabled_for_new_ingress=p_enabled
  WHERE e.agent_key=p_agent_key AND e.definition_revision=p_definition_revision;
 RETURN jsonb_build_object('outcome','applied','enabled_for_new_ingress',p_enabled);
END $$;
CREATE FUNCTION ensure_agent_runtime_definition_fact(
 p_agent_key TEXT,p_definition_revision TEXT,p_definition_hash TEXT,
 p_prompt_revision TEXT,p_catalog_revision TEXT,p_effective_toolset_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE current_fact agent_runtime_definition_facts%ROWTYPE;
BEGIN
 SELECT * INTO current_fact FROM agent_runtime_definition_facts
  WHERE agent_key=p_agent_key AND definition_revision=p_definition_revision;
IF current_fact.agent_key IS NULL OR NOT current_fact.enabled_for_new_ingress
    OR current_fact.definition_hash IS DISTINCT FROM p_definition_hash
    OR current_fact.prompt_revision IS DISTINCT FROM p_prompt_revision
    OR current_fact.catalog_revision IS DISTINCT FROM p_catalog_revision
    THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_DEFINITION_FACT_MISMATCH' USING ERRCODE='22023';
 END IF;
 UPDATE agent_runtime_definition_facts SET used_by_ingress=TRUE
  WHERE agent_key=current_fact.agent_key AND definition_revision=current_fact.definition_revision;
 RETURN to_jsonb(current_fact);
END $$;
CREATE FUNCTION get_agent_runtime_definition_fact(
 p_agent_key TEXT,p_definition_revision TEXT
) RETURNS JSONB LANGUAGE SQL STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
 SELECT COALESCE((SELECT to_jsonb(f) FROM agent_runtime_definition_facts f
   WHERE f.agent_key=p_agent_key AND f.definition_revision=p_definition_revision),
   jsonb_build_object('outcome','not_found'))
$$;
CREATE FUNCTION runtime_submit_ingress_v2( p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,p_created_by_user_id UUID,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,p_through_message_id UUID,p_base_context_revision TEXT,p_effective_toolset_revision TEXT,p_effective_toolset_hash TEXT,p_config_snapshot JSONB,p_capability_snapshot JSONB,p_release_revision TEXT,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
 SET search_path = pg_catalog, public AS $$
DECLARE ctl agent_runtime_control%ROWTYPE; d_fact agent_runtime_definition_facts%ROWTYPE; cat_fact agent_runtime_catalog_facts%ROWTYPE; tool_fact agent_runtime_effective_toolset_facts%ROWTYPE; session_fact agent_runtime_sessions%ROWTYPE; s JSONB; c JSONB; sid UUID; v_gate_state TEXT; envelope JSONB; prior agent_session_commands%ROWTYPE; prior_identity JSONB; config JSONB; capabilities JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 p_idempotency_key:=btrim(p_idempotency_key);
 IF NULLIF(btrim(p_agent_definition_id),'') IS NULL
    OR NULLIF(btrim(p_agent_definition_revision),'') IS NULL
    OR NULLIF(btrim(p_effective_toolset_revision),'') IS NULL
    OR NULLIF(btrim(p_release_revision),'') IS NULL
    OR NULLIF(btrim(p_scope_kind),'') IS NULL
    OR p_scope_kind NOT IN ('user','channel')
    OR NULLIF(btrim(p_scope_id),'') IS NULL
    OR NULLIF(btrim(p_idempotency_key),'') IS NULL
    OR p_command_type IS DISTINCT FROM 'submit_input'
    OR jsonb_typeof(COALESCE(p_payload,'{}'::JSONB)) IS DISTINCT FROM 'object'
    OR NULLIF(btrim(p_base_context_revision),'') IS NULL
    OR p_base_context_revision IS DISTINCT FROM 'message:'||p_through_message_id::TEXT
    OR jsonb_typeof(p_config_snapshot) IS DISTINCT FROM 'object'
    OR jsonb_typeof(p_capability_snapshot) IS DISTINCT FROM 'object' THEN
   RAISE EXCEPTION 'RUNTIME_INGRESS_V2_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 IF p_through_message_id IS NULL OR p_channel NOT IN ('web','wecom') THEN
   RAISE EXCEPTION 'RUNTIME_INGRESS_V2_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO session_fact FROM agent_runtime_sessions
  WHERE conversation_id=p_conversation_id FOR UPDATE;
 IF FOUND THEN
   sid:=session_fact.id;
   IF session_fact.org_id IS DISTINCT FROM p_org_id
      OR session_fact.user_id IS DISTINCT FROM p_user_id
      OR session_fact.scope_kind IS DISTINCT FROM p_scope_kind
      OR session_fact.scope_id IS DISTINCT FROM p_scope_id THEN
     RAISE EXCEPTION 'AGENT_RUNTIME_SESSION_CONFLICT' USING ERRCODE='23505';
   END IF;
   SELECT * INTO prior FROM agent_session_commands
    WHERE session_id=sid AND command_type=p_command_type
      AND idempotency_key=p_idempotency_key FOR UPDATE;
   IF prior.id IS NOT NULL THEN
    prior_identity:=prior.payload->'run_envelope'->'request_identity';
    IF prior_identity->>'session_id' IS DISTINCT FROM sid::TEXT
       OR prior_identity->>'idempotency_key' IS DISTINCT FROM p_idempotency_key
       OR prior_identity->>'conversation_id' IS DISTINCT FROM p_conversation_id::TEXT
       OR prior_identity->>'user_id' IS DISTINCT FROM p_user_id::TEXT
       OR prior_identity->>'org_id' IS DISTINCT FROM p_org_id::TEXT
       OR prior_identity->>'scope_kind' IS DISTINCT FROM p_scope_kind
       OR prior_identity->>'scope_id' IS DISTINCT FROM p_scope_id
       OR prior_identity->>'through_message_id' IS DISTINCT FROM p_through_message_id::TEXT
       OR prior_identity->>'base_context_revision' IS DISTINCT FROM p_base_context_revision
       OR prior_identity->>'agent_definition_id' IS DISTINCT FROM p_agent_definition_id
       OR prior_identity->>'agent_definition_revision' IS DISTINCT FROM p_agent_definition_revision
       OR prior_identity->>'agent_definition_hash' IS DISTINCT FROM p_agent_definition_hash
       OR prior_identity->>'catalog_revision' IS DISTINCT FROM p_effective_toolset_revision
       OR prior_identity->>'payload_hash' IS DISTINCT FROM md5(COALESCE(p_payload,'{}'::jsonb)::TEXT)
       OR prior_identity->>'config_snapshot_hash' IS DISTINCT FROM md5(p_config_snapshot::TEXT)
       OR prior_identity->>'channel' IS DISTINCT FROM p_channel THEN
      RETURN jsonb_build_object('outcome','idempotency_conflict',
        'entity_id',prior.id,'session_id',sid,'ingress_version',2);
    END IF;
    RETURN jsonb_build_object('outcome','already_exists','entity_id',prior.id,
      'result_entity_id',prior.result_entity_id,'session_id',sid,
      'effective_toolset_revision',prior.payload->'run_envelope'->'capability_snapshot'->>'effective_toolset_revision',
      'effective_toolset_hash',prior.payload->'run_envelope'->'capability_snapshot'->>'effective_toolset_hash',
      'gate_state',prior.payload->'run_envelope'->'capability_snapshot'->>'gate_state',
      'ingress_version',2);
   END IF;
 END IF;
 SELECT * INTO ctl FROM agent_runtime_control WHERE singleton FOR UPDATE;
 IF NOT ctl.ingress_enabled THEN
   RETURN jsonb_build_object('outcome','ingress_disabled');
 END IF;
 IF p_org_id IS NULL OR NOT EXISTS(
   SELECT 1 FROM agent_runtime_org_rollout WHERE org_id=p_org_id AND enabled
 ) THEN
   RETURN jsonb_build_object('outcome','org_not_enabled');
 END IF;
 IF NOT EXISTS (SELECT 1 FROM messages m WHERE m.id=p_through_message_id
       AND m.conversation_id=p_conversation_id
       AND m.org_id IS NOT DISTINCT FROM p_org_id) THEN
   RAISE EXCEPTION 'RUNTIME_CONTEXT_ANCHOR_MISSING' USING ERRCODE='22023';
 END IF;
 s:=ensure_agent_runtime_session(p_conversation_id,p_org_id,p_user_id,
   p_scope_kind,p_scope_id,p_created_by_user_id,p_agent_definition_id,
   p_agent_definition_revision);
 IF s->>'outcome' NOT IN ('created','already_exists') THEN RETURN s; END IF;
 sid:=(s->>'entity_id')::uuid;
 SELECT * INTO d_fact FROM agent_runtime_definition_facts
  WHERE agent_key=p_agent_definition_id AND definition_revision=p_agent_definition_revision;
 SELECT * INTO cat_fact FROM agent_runtime_catalog_facts
  WHERE catalog_revision=COALESCE(d_fact.catalog_revision,p_effective_toolset_revision);
 IF d_fact.agent_key IS NULL OR NOT d_fact.enabled_for_new_ingress
    OR NOT d_fact.recoverable OR d_fact.definition_hash IS DISTINCT FROM p_agent_definition_hash
    OR p_effective_toolset_revision IS DISTINCT FROM d_fact.catalog_revision
    OR cat_fact.catalog_revision IS NULL OR NOT cat_fact.enabled_for_new_ingress
    OR NOT cat_fact.recoverable THEN
   RAISE EXCEPTION 'RUNTIME_VERSION_FACT_NOT_ENABLED' USING ERRCODE='55000';
 END IF;
 v_gate_state:='disabled';
 IF ctl.non_safe_actions_enabled AND ctl.code_execute_enabled
    AND ctl.tool_confirmation_enabled
    AND EXISTS (SELECT 1 FROM agent_runtime_capabilities cap
      WHERE cap.capability_name='tool_confirmation_v3_redis' AND cap.ready
        AND cap.observed_at>clock_timestamp()-interval '60 seconds')
    AND EXISTS (SELECT 1 FROM agent_runtime_worker_heartbeats h
      WHERE h.process_role='sandbox' AND h.ready AND NOT h.draining
        AND h.observed_at>clock_timestamp()-interval '30 seconds') THEN
   v_gate_state:='enabled';
 END IF;
 SELECT * INTO tool_fact FROM agent_runtime_effective_toolset_facts
  WHERE agent_key=d_fact.agent_key AND definition_revision=d_fact.definition_revision
    AND catalog_revision=d_fact.catalog_revision AND scope_kind=p_scope_kind
    AND channel=p_channel AND agent_runtime_effective_toolset_facts.gate_state=v_gate_state;
 IF tool_fact.effective_toolset_hash IS NULL
    OR NOT tool_fact.enabled_for_new_ingress OR NOT tool_fact.recoverable THEN
   RAISE EXCEPTION 'RUNTIME_EFFECTIVE_TOOLSET_FACT_MISSING' USING ERRCODE='55000';
 END IF;
 PERFORM ensure_agent_runtime_definition_fact(
   p_agent_definition_id,p_agent_definition_revision,p_agent_definition_hash,
   d_fact.prompt_revision,d_fact.catalog_revision,tool_fact.effective_toolset_hash);
 config:=p_config_snapshot||jsonb_build_object(
   'base_context_revision',p_base_context_revision,
   'through_message_id',p_through_message_id,
   'agent_definition_revision',p_agent_definition_revision,
   'agent_definition_hash',p_agent_definition_hash,
   'tool_catalog_revision',d_fact.catalog_revision,
   'tool_catalog_hash',cat_fact.catalog_hash,
   'effective_toolset_revision',d_fact.catalog_revision,
   'effective_toolset_hash',tool_fact.effective_toolset_hash,
   'release_revision',p_release_revision,
   'config_snapshot_hash',md5(p_config_snapshot::TEXT));
 capabilities:=jsonb_build_object(
   'channel',p_channel,'agent_definition_id',p_agent_definition_id,
   'agent_definition_revision',p_agent_definition_revision,
   'agent_definition_hash',p_agent_definition_hash,
   'tool_catalog_revision',d_fact.catalog_revision,
   'tool_catalog_hash',cat_fact.catalog_hash,
   'effective_toolset_revision',d_fact.catalog_revision,
   'effective_toolset_hash',tool_fact.effective_toolset_hash,
   'gate_state',v_gate_state,
   'capability_snapshot_hash',md5(COALESCE(p_capability_snapshot,'{}'::jsonb)::TEXT));
 envelope:=jsonb_build_object(
   'schema_revision',2,'run_kind','user',
   'context_receipt',jsonb_build_object(
     'base_context_revision',p_base_context_revision,
     'through_message_id',p_through_message_id,
     'session_id',sid,'conversation_id',p_conversation_id),
   'config_snapshot',config,'capability_snapshot',capabilities,
   'request_identity',jsonb_build_object(
   'session_id',sid,'idempotency_key',p_idempotency_key,
     'channel',p_channel,'conversation_id',p_conversation_id,
     'user_id',p_user_id,'org_id',p_org_id,'scope_kind',p_scope_kind,
     'scope_id',p_scope_id,'through_message_id',p_through_message_id,
     'base_context_revision',p_base_context_revision,
     'agent_definition_id',p_agent_definition_id,
     'agent_definition_revision',p_agent_definition_revision,
     'agent_definition_hash',p_agent_definition_hash,
     'catalog_revision',d_fact.catalog_revision,
     'effective_toolset_hash',tool_fact.effective_toolset_hash,
     'config_snapshot_hash',md5(p_config_snapshot::TEXT),
     'payload_hash',md5(COALESCE(p_payload,'{}'::jsonb)::TEXT),
     'binding_hash',md5(jsonb_build_object(
       'conversation_id',p_conversation_id,'user_id',p_user_id,
       'org_id',p_org_id,'scope_kind',p_scope_kind,'scope_id',p_scope_id,
       'through_message_id',p_through_message_id,
       'base_context_revision',p_base_context_revision,
     'agent_definition_hash',p_agent_definition_hash,
     'effective_toolset_hash',tool_fact.effective_toolset_hash)::TEXT)));
 c:=submit_session_command(sid,p_command_type,p_idempotency_key,
   COALESCE(p_payload,'{}'::jsonb)||jsonb_build_object(
     'run_envelope',envelope,'release_revision',p_release_revision));
 IF c->>'outcome' IN ('created','already_exists') THEN
   UPDATE tasks SET delivery_context=delivery_context||
     jsonb_build_object('actor',false,'runtime',true,'runtime_ingress_version',2)
    WHERE id=NULLIF(p_payload->>'task_id','')::uuid
      AND conversation_id=p_conversation_id AND user_id=p_user_id
      AND org_id IS NOT DISTINCT FROM p_org_id;
 END IF;
 RETURN c||jsonb_build_object('session_id',sid,'ingress_version',2,
   'effective_toolset_revision',d_fact.catalog_revision,
   'effective_toolset_hash',tool_fact.effective_toolset_hash,
   'gate_state',v_gate_state);
END $$;
CREATE FUNCTION get_agent_runtime_model_context_v2(
 p_run_id UUID,p_worker_id TEXT,p_execution_token UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
 SET search_path = pg_catalog, public AS $$
DECLARE r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
 c agent_session_commands%ROWTYPE; anchor messages%ROWTYPE;
 d_fact agent_runtime_definition_facts%ROWTYPE;
 cat_fact agent_runtime_catalog_facts%ROWTYPE;
 tool_fact agent_runtime_effective_toolset_facts%ROWTYPE;
 v_messages JSONB; v_context_hash TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO r FROM agent_runs WHERE id=p_run_id;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
 SELECT * INTO c FROM agent_session_commands WHERE id=r.command_id;
 IF r.id IS NULL OR s.id IS NULL OR c.id IS NULL OR r.status<>'running'
   OR r.execution_token IS DISTINCT FROM p_execution_token
   OR r.lease_expires_at<=clock_timestamp()
   OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra WHERE ra.run_id=r.id
      AND ra.execution_token=p_execution_token AND ra.worker_id=btrim(p_worker_id)
      AND ra.ended_at IS NULL) THEN
  RETURN jsonb_build_object('outcome','ownership_lost');
 END IF;
 IF (r.context_receipt->>'base_context_revision') IS DISTINCT FROM
      ('message:' || (r.context_receipt->>'through_message_id'))
    OR r.context_receipt->>'through_message_id' IS NULL
    OR r.context_receipt->>'session_id' IS DISTINCT FROM s.id::TEXT
    OR r.context_receipt->>'conversation_id' IS DISTINCT FROM s.conversation_id::TEXT
    OR r.config_snapshot IS DISTINCT FROM c.payload->'run_envelope'->'config_snapshot'
    OR r.capability_snapshot IS DISTINCT FROM c.payload->'run_envelope'->'capability_snapshot'
    OR c.payload->>'release_revision' IS DISTINCT FROM
       r.config_snapshot->>'release_revision'
    OR r.config_snapshot->>'base_context_revision' IS DISTINCT FROM
      r.context_receipt->>'base_context_revision'
    OR r.config_snapshot->>'through_message_id' IS DISTINCT FROM
      r.context_receipt->>'through_message_id'
    OR c.payload->'run_envelope'->>'schema_revision' IS DISTINCT FROM '2'
    OR c.payload->'run_envelope'->'context_receipt'->>'through_message_id'
       IS DISTINCT FROM r.context_receipt->>'through_message_id'
    OR NOT EXISTS (SELECT 1 FROM agent_runtime_definition_facts f
       WHERE f.agent_key=s.agent_definition_id
         AND f.definition_revision=s.agent_definition_revision
         AND f.recoverable) THEN
  RETURN jsonb_build_object('outcome','context_revision_mismatch');
 END IF;
 SELECT * INTO d_fact FROM agent_runtime_definition_facts
  WHERE agent_key=s.agent_definition_id
    AND definition_revision=s.agent_definition_revision
    AND definition_hash=r.capability_snapshot->>'agent_definition_hash'
    AND recoverable;
 SELECT * INTO cat_fact FROM agent_runtime_catalog_facts
  WHERE catalog_revision=r.capability_snapshot->>'tool_catalog_revision'
    AND catalog_hash=r.capability_snapshot->>'tool_catalog_hash'
    AND recoverable;
 SELECT * INTO tool_fact FROM agent_runtime_effective_toolset_facts
  WHERE agent_key=s.agent_definition_id
    AND definition_revision=s.agent_definition_revision
    AND catalog_revision=r.capability_snapshot->>'tool_catalog_revision'
    AND scope_kind=s.scope_kind AND channel=r.capability_snapshot->>'channel'
    AND gate_state=r.capability_snapshot->>'gate_state'
    AND effective_toolset_hash=r.capability_snapshot->>'effective_toolset_hash'
    AND recoverable;
 IF d_fact.agent_key IS NULL OR cat_fact.catalog_revision IS NULL
    OR tool_fact.effective_toolset_hash IS NULL
    OR d_fact.catalog_revision IS DISTINCT FROM cat_fact.catalog_revision
    OR d_fact.definition_hash IS DISTINCT FROM r.capability_snapshot->>'agent_definition_hash'
    OR d_fact.catalog_revision IS DISTINCT FROM r.capability_snapshot->>'effective_toolset_revision' THEN
  RETURN jsonb_build_object('outcome','context_revision_mismatch');
 END IF;
 SELECT * INTO anchor FROM messages WHERE id=NULLIF(
   r.context_receipt->>'through_message_id','')::uuid
   AND conversation_id=s.conversation_id
   AND org_id IS NOT DISTINCT FROM s.org_id;
 IF anchor.id IS NULL THEN
  RETURN jsonb_build_object('outcome','context_anchor_missing');
 END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object(
      'id',m.id,'role',m.role,'content',m.content,'turn_id',m.turn_id)
      ORDER BY m.created_at,m.id),'[]'::jsonb) INTO v_messages
   FROM messages m
   WHERE m.conversation_id=s.conversation_id
     AND m.org_id IS NOT DISTINCT FROM s.org_id
     AND m.status='completed' AND (m.created_at,m.id)<=(anchor.created_at,anchor.id);
 v_context_hash:=encode(sha256(convert_to(v_messages::TEXT,'UTF8')),'hex');
 RETURN jsonb_build_object('outcome','found','session',to_jsonb(s),
  'run',to_jsonb(r),'command',to_jsonb(c),
  'task',(SELECT to_jsonb(t) FROM tasks t WHERE t.id=NULLIF(c.payload->>'task_id','')::uuid
      AND t.conversation_id=s.conversation_id),
  'messages',v_messages,'context_hash',v_context_hash,
  'definition_fact',to_jsonb(d_fact),
  'catalog_fact',to_jsonb(cat_fact),
  'effective_toolset_fact',to_jsonb(tool_fact),
  'actions',(SELECT coalesce(jsonb_agg(to_jsonb(a)||jsonb_build_object('result',
      (SELECT to_jsonb(ar) FROM agent_action_results ar WHERE ar.action_id=a.id))
      ORDER BY a.model_step_id,a.action_index,a.id),'[]'::jsonb)
      FROM agent_actions a WHERE a.run_id=r.id));
END $$;
CREATE FUNCTION enqueue_wecom_runtime_turn_v4(
 p_task_data JSONB,p_input_message_id UUID,p_output_message_id UUID,
 p_turn_id UUID,p_input_content JSONB,p_delivery_context JSONB,
 p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_agent_definition_hash TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_release_revision TEXT,
 p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
 SET search_path = pg_catalog, public AS $$
DECLARE e JSONB; r JSONB; conversation_id UUID; user_id UUID; org_id UUID;
 d JSONB; scope_kind TEXT; scope_id TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 e:=enqueue_wecom_generation_turn_v2(p_task_data,p_input_message_id,
   p_output_message_id,p_turn_id,p_input_content,p_delivery_context);
 SELECT t.conversation_id,t.user_id,t.org_id INTO conversation_id,user_id,org_id
   FROM tasks t WHERE t.id=(e->>'task_id')::uuid FOR UPDATE;
 SELECT c.scope_type,c.scope_id INTO scope_kind,scope_id
   FROM conversations c WHERE c.id=conversation_id;
 r:=runtime_submit_ingress_v2(conversation_id,org_id,user_id,scope_kind,scope_id,
   user_id,p_agent_definition_id,p_agent_definition_revision,p_agent_definition_hash,
   'submit_input',p_idempotency_key,'wecom',p_input_message_id,
   'message:'||p_input_message_id,p_effective_toolset_revision,
   p_effective_toolset_hash,'{}'::jsonb,
   jsonb_build_object('requested_groups',jsonb_build_array('code')),
   p_release_revision,jsonb_build_object(
    'schema_revision',2,'channel','wecom','task_id',e->>'task_id',
    'input_message_id',p_input_message_id,'output_message_id',p_output_message_id,
    'turn_id',p_turn_id,'content',p_input_content,
    'delivery_context',p_delivery_context||'{"actor":false,"runtime":true}'::jsonb));
 IF r->>'outcome' IN ('ingress_disabled','org_not_enabled') THEN
   RETURN e||jsonb_build_object('runtime_owned',false);
 END IF;
 IF r->>'outcome' NOT IN('created','already_exists') THEN
   RAISE EXCEPTION 'WECOM_RUNTIME_INGRESS_V4_FAILED: %',r->>'outcome'
     USING ERRCODE='55000';
 END IF;
 d:=p_delivery_context||'{"actor":false,"runtime":true}'::jsonb;
 UPDATE tasks SET delivery_context=d WHERE id=(e->>'task_id')::uuid;
 RETURN e||jsonb_build_object('runtime_owned',true,
   'runtime_session_id',r->>'session_id','runtime_command_id',r->>'entity_id',
   'effective_toolset_revision',r->>'effective_toolset_revision',
   'effective_toolset_hash',r->>'effective_toolset_hash',
   'gate_state',r->>'gate_state');
END $$;
REVOKE ALL ON FUNCTION runtime_submit_ingress_v2(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,
 TEXT,TEXT,JSONB,JSONB,TEXT,JSONB),
  get_agent_runtime_model_context_v2(UUID,TEXT,UUID),
  ensure_agent_runtime_definition_fact(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
  get_agent_runtime_definition_fact(TEXT,TEXT),
  get_agent_runtime_version_facts(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
  set_agent_runtime_definition_ingress_enabled(TEXT,TEXT,BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_wecom_runtime_turn_v4(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
 FROM PUBLIC,everydayai_runtime,everydayai_worker,everydayai_wecom_runtime,
 everydayai_sync,everydayai;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v2(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,
 TEXT,TEXT,JSONB,JSONB,TEXT,JSONB) TO everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION get_agent_runtime_model_context_v2(UUID,TEXT,UUID)
 TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION get_agent_runtime_definition_fact(TEXT,TEXT)
 TO everydayai_agent_runtime_worker,everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION get_agent_runtime_version_facts(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
 TO everydayai_agent_runtime_worker,everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION set_agent_runtime_definition_ingress_enabled(TEXT,TEXT,BOOLEAN)
 TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION enqueue_wecom_runtime_turn_v4(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
 TO everydayai_wecom_runtime;
RESET ROLE;
