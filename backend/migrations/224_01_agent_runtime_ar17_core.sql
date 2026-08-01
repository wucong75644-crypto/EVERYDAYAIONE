-- 224_01: additive AR-17.1 frozen ingress and Run-bound context facts.
-- 212-223 remain immutable; Run creation still belongs to 219 claim.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_definition_facts (
    agent_key TEXT NOT NULL,
    definition_revision TEXT NOT NULL,
    definition_hash TEXT NOT NULL CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
    prompt_revision TEXT NOT NULL,
    catalog_revision TEXT NOT NULL CHECK (catalog_revision ~ '^[0-9a-f]{64}$'),
    effective_toolset_hash TEXT NOT NULL CHECK (effective_toolset_hash ~ '^[0-9a-f]{64}$'),
    active BOOLEAN NOT NULL DEFAULT TRUE,
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

CREATE FUNCTION _agent_runtime_224_expected_facts()
RETURNS JSONB LANGUAGE SQL IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
 SELECT jsonb_build_object(
   'agent_key','everydayai-default','definition_revision','v1',
   'definition_hash','b5818e976876aa8c0ead0b50ebea8439fe0e230e9d55dfac9e7d5580d18895ff',
   'prompt_revision','agent-runtime-production-v1',
   'catalog_revision','7e449bf4ca2a4827d5fa96df4721c4978d9a1d96e0215012500669e5ac2eb131',
   'effective_toolset_hash','897d940de4aa6ebca9a5df0197824ac906f6cea2469461c5ec0ae88e595d90fc'
 )
$$;

CREATE FUNCTION ensure_agent_runtime_definition_fact(
 p_agent_key TEXT,p_definition_revision TEXT,p_definition_hash TEXT,
 p_prompt_revision TEXT,p_catalog_revision TEXT,p_effective_toolset_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE expected JSONB; current_fact agent_runtime_definition_facts%ROWTYPE;
BEGIN
 expected:=_agent_runtime_224_expected_facts();
 IF jsonb_build_object(
      'agent_key',p_agent_key,'definition_revision',p_definition_revision,
      'definition_hash',p_definition_hash,'prompt_revision',p_prompt_revision,
      'catalog_revision',p_catalog_revision,
      'effective_toolset_hash',p_effective_toolset_hash) IS DISTINCT FROM expected THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_DEFINITION_FACT_MISMATCH' USING ERRCODE='22023';
 END IF;
 INSERT INTO agent_runtime_definition_facts(
   agent_key,definition_revision,definition_hash,prompt_revision,
   catalog_revision,effective_toolset_hash)
 VALUES(p_agent_key,p_definition_revision,p_definition_hash,p_prompt_revision,
        p_catalog_revision,p_effective_toolset_hash)
 ON CONFLICT (agent_key,definition_revision) DO NOTHING;
 SELECT * INTO current_fact FROM agent_runtime_definition_facts
  WHERE agent_key=p_agent_key AND definition_revision=p_definition_revision;
 IF current_fact.definition_hash IS DISTINCT FROM p_definition_hash
    OR current_fact.catalog_revision IS DISTINCT FROM p_catalog_revision
    OR current_fact.effective_toolset_hash IS DISTINCT FROM p_effective_toolset_hash
    OR NOT current_fact.active THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_DEFINITION_FACT_CONFLICT' USING ERRCODE='23505';
 END IF;
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

CREATE FUNCTION runtime_submit_ingress_v2(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,
 p_scope_id TEXT,p_created_by_user_id UUID,p_agent_definition_id TEXT,
 p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,
 p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,
 p_through_message_id UUID,p_base_context_revision TEXT,
 p_effective_toolset_revision TEXT,p_effective_toolset_hash TEXT,
 p_config_snapshot JSONB,p_capability_snapshot JSONB,p_release_revision TEXT,
 p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
 SET search_path = pg_catalog, public AS $$
DECLARE ctl agent_runtime_control%ROWTYPE; s JSONB; c JSONB; sid UUID;
    envelope JSONB; config JSONB; capabilities JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 SELECT * INTO ctl FROM agent_runtime_control WHERE singleton FOR SHARE;
 IF NOT ctl.ingress_enabled THEN
   RETURN jsonb_build_object('outcome','ingress_disabled');
 END IF;
 IF p_org_id IS NULL OR NOT EXISTS(
   SELECT 1 FROM agent_runtime_org_rollout WHERE org_id=p_org_id AND enabled
 ) THEN
   RETURN jsonb_build_object('outcome','org_not_enabled');
 END IF;
 IF p_agent_definition_id IS DISTINCT FROM 'everydayai-default'
    OR p_agent_definition_revision IS DISTINCT FROM 'v1'
    OR p_agent_definition_hash IS DISTINCT FROM
      (_agent_runtime_224_expected_facts()->>'definition_hash')
    OR NULLIF(btrim(p_effective_toolset_revision),'') IS NULL
    OR p_effective_toolset_revision IS DISTINCT FROM
      (_agent_runtime_224_expected_facts()->>'catalog_revision')
    OR p_effective_toolset_hash IS DISTINCT FROM
      (_agent_runtime_224_expected_facts()->>'effective_toolset_hash')
    OR NULLIF(btrim(p_release_revision),'') IS NULL
    OR NULLIF(btrim(p_scope_kind),'') IS NULL
    OR p_scope_kind NOT IN ('user','channel')
    OR NULLIF(btrim(p_scope_id),'') IS NULL
    OR NULLIF(btrim(p_idempotency_key),'') IS NULL
    OR p_command_type IS DISTINCT FROM 'submit_input'
    OR jsonb_typeof(COALESCE(p_payload,'{}'::JSONB)) IS DISTINCT FROM 'object'
    OR NULLIF(btrim(p_base_context_revision),'') IS NULL
    OR p_base_context_revision IS DISTINCT FROM 'message:'||p_through_message_id::TEXT
    OR NOT EXISTS (SELECT 1 FROM messages m WHERE m.id=p_through_message_id
       AND m.conversation_id=p_conversation_id
       AND m.org_id IS NOT DISTINCT FROM p_org_id)
    OR jsonb_typeof(p_config_snapshot) IS DISTINCT FROM 'object'
    OR jsonb_typeof(p_capability_snapshot) IS DISTINCT FROM 'object' THEN
   RAISE EXCEPTION 'RUNTIME_INGRESS_V2_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 IF p_through_message_id IS NULL OR p_channel NOT IN ('web','wecom') THEN
   RAISE EXCEPTION 'RUNTIME_INGRESS_V2_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 PERFORM ensure_agent_runtime_definition_fact(
   p_agent_definition_id,p_agent_definition_revision,p_agent_definition_hash,
   'agent-runtime-production-v1',p_effective_toolset_revision,
   p_effective_toolset_hash);
 s:=ensure_agent_runtime_session(p_conversation_id,p_org_id,p_user_id,
   p_scope_kind,p_scope_id,p_created_by_user_id,p_agent_definition_id,
   p_agent_definition_revision);
 IF s->>'outcome' NOT IN ('created','already_exists') THEN RETURN s; END IF;
 sid:=(s->>'entity_id')::uuid;
 config:=p_config_snapshot||jsonb_build_object(
   'base_context_revision',p_base_context_revision,
   'through_message_id',p_through_message_id,
   'agent_definition_hash',p_agent_definition_hash,
   'effective_toolset_revision',p_effective_toolset_revision,
   'effective_toolset_hash',p_effective_toolset_hash,
   'release_revision',p_release_revision,
   'config_snapshot_hash',md5(p_config_snapshot::TEXT));
 capabilities:=p_capability_snapshot||jsonb_build_object(
   'channel',p_channel,'agent_definition_id',p_agent_definition_id,
   'agent_definition_revision',p_agent_definition_revision,
   'agent_definition_hash',p_agent_definition_hash,
   'effective_toolset_revision',p_effective_toolset_revision,
   'effective_toolset_hash',p_effective_toolset_hash,
   'capability_snapshot_hash',md5(p_capability_snapshot::TEXT));
 envelope:=jsonb_build_object(
   'schema_revision',2,'run_kind','user',
   'context_receipt',jsonb_build_object(
     'base_context_revision',p_base_context_revision,
     'through_message_id',p_through_message_id,
     'session_id',sid,'conversation_id',p_conversation_id),
   'config_snapshot',config,'capability_snapshot',capabilities,
   'request_identity',jsonb_build_object(
     'session_id',sid,'idempotency_key',p_idempotency_key,
     'channel',p_channel,'binding_hash',md5(jsonb_build_object(
       'conversation_id',p_conversation_id,'user_id',p_user_id,
       'org_id',p_org_id,'scope_kind',p_scope_kind,'scope_id',p_scope_id,
       'through_message_id',p_through_message_id,
       'base_context_revision',p_base_context_revision,
       'agent_definition_hash',p_agent_definition_hash,
       'effective_toolset_hash',p_effective_toolset_hash)::TEXT)));
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
 RETURN c||jsonb_build_object('session_id',sid,'ingress_version',2);
END $$;

CREATE FUNCTION get_agent_runtime_model_context_v2(
 p_run_id UUID,p_worker_id TEXT,p_execution_token UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
 SET search_path = pg_catalog, public AS $$
DECLARE r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
 c agent_session_commands%ROWTYPE; anchor messages%ROWTYPE;
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
    OR r.capability_snapshot->>'effective_toolset_revision' IS DISTINCT FROM
      (_agent_runtime_224_expected_facts()->>'catalog_revision')
   OR r.capability_snapshot->>'effective_toolset_hash' IS DISTINCT FROM
      (_agent_runtime_224_expected_facts()->>'effective_toolset_hash')
    OR r.config_snapshot->>'agent_definition_hash' IS DISTINCT FROM
       (_agent_runtime_224_expected_facts()->>'definition_hash')
    OR r.capability_snapshot->>'agent_definition_hash' IS DISTINCT FROM
       (_agent_runtime_224_expected_facts()->>'definition_hash')
    OR c.payload->'run_envelope'->>'schema_revision' IS DISTINCT FROM '2'
    OR c.payload->'run_envelope'->'context_receipt'->>'through_message_id'
       IS DISTINCT FROM r.context_receipt->>'through_message_id'
    OR NOT EXISTS (SELECT 1 FROM agent_runtime_definition_facts f
       WHERE f.agent_key=s.agent_definition_id
         AND f.definition_revision=s.agent_definition_revision
         AND f.active) THEN
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
   'runtime_session_id',r->>'session_id','runtime_command_id',r->>'entity_id');
END $$;

REVOKE ALL ON FUNCTION runtime_submit_ingress_v2(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,
 TEXT,TEXT,JSONB,JSONB,TEXT,JSONB),
  get_agent_runtime_model_context_v2(UUID,TEXT,UUID),
  ensure_agent_runtime_definition_fact(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
  get_agent_runtime_definition_fact(TEXT,TEXT) FROM PUBLIC;
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
GRANT EXECUTE ON FUNCTION enqueue_wecom_runtime_turn_v4(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
 TO everydayai_wecom_runtime;

RESET ROLE;
