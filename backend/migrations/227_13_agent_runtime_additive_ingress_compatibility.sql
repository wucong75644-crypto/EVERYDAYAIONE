-- 227.13: additive ingress compatibility lane.
-- 227.01 through 227.12 remain immutable; production binding facts are not
-- changed and provider readiness remains an action-dispatch concern.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_ingress_kill_epoch_context(p_org_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE g agent_runtime_tenant_gate_controls%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_org_id IS NULL THEN
        RETURN jsonb_build_object('outcome','allowed','kill_epoch',0);
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'agent-runtime-kill-gate:'||p_org_id::TEXT||':tenant:tenant',0));
    SELECT * INTO g FROM agent_runtime_tenant_gate_controls
     WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
    IF FOUND AND g.ingress_blocked THEN
        RETURN jsonb_build_object('outcome','fenced',
            'error_code','RUNTIME_KILL_EPOCH_FENCED','kill_epoch',g.kill_epoch);
    END IF;
    RETURN jsonb_build_object('outcome','allowed',
        'kill_epoch',COALESCE(g.kill_epoch,0));
END $$;

CREATE FUNCTION get_agent_runtime_ingress_capability()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
    SELECT jsonb_build_object('outcome','available','ingress_version',5)
$$;

CREATE FUNCTION runtime_submit_ingress_v5(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,
 p_created_by_user_id UUID,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_agent_definition_hash TEXT,p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,
 p_through_message_id UUID,p_base_context_revision TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_config_snapshot JSONB,p_capability_snapshot JSONB,
 p_release_revision TEXT,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ctl agent_runtime_control%ROWTYPE; s JSONB; sid UUID; d agent_runtime_definition_facts%ROWTYPE;
 c agent_runtime_catalog_facts%ROWTYPE; t agent_runtime_effective_toolset_facts%ROWTYPE;
 v_gate TEXT; command_result JSONB; config JSONB; capabilities JSONB; rollout_ok BOOLEAN; kill JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 kill:=_agent_runtime_ingress_kill_epoch_context(p_org_id);
 IF kill->>'outcome' <> 'allowed' THEN
   RETURN jsonb_build_object('outcome','ingress_disabled','error_code',kill->>'error_code','ingress_version',5);
 END IF;
 IF p_scope_kind NOT IN ('user','channel') OR p_channel NOT IN ('web','wecom')
    OR p_through_message_id IS NULL OR p_base_context_revision IS DISTINCT FROM 'message:'||p_through_message_id::TEXT
    OR NULLIF(btrim(p_scope_id),'') IS NULL OR NULLIF(btrim(p_idempotency_key),'') IS NULL
    OR p_command_type IS DISTINCT FROM 'submit_input'
    OR jsonb_typeof(COALESCE(p_payload,'{}'::JSONB)) IS DISTINCT FROM 'object' THEN
   RAISE EXCEPTION 'RUNTIME_INGRESS_V5_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT r.enabled AND EXISTS(
   SELECT 1 FROM jsonb_array_elements(r.capabilities) x WHERE x #>>'{}'='runtime_ingress'
 ) INTO rollout_ok FROM agent_runtime_rollout_subjects r
 WHERE r.channel=p_channel AND r.enabled AND (
   (r.subject_kind='user' AND r.subject_id=p_user_id::TEXT) OR
   (p_org_id IS NOT NULL AND r.subject_kind='organization' AND r.subject_id=p_org_id::TEXT)
 ) LIMIT 1;
 IF NOT COALESCE(rollout_ok,FALSE) THEN RETURN jsonb_build_object('outcome','subject_not_enabled','ingress_version',5); END IF;
 SELECT * INTO ctl FROM agent_runtime_control WHERE singleton FOR UPDATE;
 IF NOT ctl.ingress_enabled THEN RETURN jsonb_build_object('outcome','ingress_disabled','ingress_version',5); END IF;
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
 RETURN command_result||jsonb_build_object('session_id',sid,'ingress_version',5,'effective_toolset_revision',d.catalog_revision,
   'effective_toolset_hash',t.effective_toolset_hash,'gate_state',v_gate);
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_ingress_kill_epoch_context(UUID),
 get_agent_runtime_ingress_capability(),
 runtime_submit_ingress_v5(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB)
 FROM PUBLIC;
GRANT EXECUTE ON FUNCTION get_agent_runtime_ingress_capability()
 TO everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v5(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB)
 TO everydayai_runtime,everydayai_wecom_runtime;

RESET ROLE;
