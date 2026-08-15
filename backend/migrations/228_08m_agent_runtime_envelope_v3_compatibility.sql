-- 228.08m: allow the current frozen Runtime envelope revision through the
-- model and narrow read-capability context boundaries while retaining v2
-- recovery compatibility.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION get_agent_runtime_model_context_v2(
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
    OR c.payload->'run_envelope'->>'schema_revision' IS NULL
    OR c.payload->'run_envelope'->>'schema_revision' NOT IN ('2','3')
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

CREATE OR REPLACE FUNCTION _agent_runtime_read_context(
    p_action_id UUID, p_attempt_id UUID, p_execution_token UUID,
    p_request_hash TEXT, p_executor_type TEXT, p_executor_revision INTEGER
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_actions%ROWTYPE; t agent_action_attempts%ROWTYPE;
        r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
        i agent_action_dispatch_intents%ROWTYPE; cmd agent_session_commands%ROWTYPE;
        anchor messages%ROWTYPE; fence BIGINT;
BEGIN
    IF session_user <> 'everydayai_agent_runtime_worker'
       OR current_setting('app.access_kind', TRUE) <> 'agent_runtime' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_WORKER_REQUIRED' USING ERRCODE='42501';
    END IF;
    SELECT action.* INTO a FROM agent_actions action
     JOIN agent_action_attempts attempt ON attempt.id=p_attempt_id
       AND attempt.action_id=action.id
     JOIN agent_runs run ON run.id=action.run_id AND run.id=attempt.run_id
     JOIN agent_runtime_sessions session ON session.id=action.session_id
       AND session.id=attempt.session_id AND session.id=run.session_id
     JOIN agent_action_dispatch_intents intent ON intent.attempt_id=attempt.id
       AND intent.action_id=action.id
     WHERE action.id=p_action_id AND action.request_hash=p_request_hash
       AND attempt.execution_token=p_execution_token
       AND intent.execution_token=p_execution_token AND intent.request_hash=p_request_hash
       AND intent.executor_type=p_executor_type AND intent.executor_revision=p_executor_revision;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_BINDING_INVALID' USING ERRCODE='42501';
    END IF;
    SELECT * INTO t FROM agent_action_attempts WHERE id=p_attempt_id;
    SELECT * INTO r FROM agent_runs WHERE id=a.run_id;
    SELECT * INTO s FROM agent_runtime_sessions WHERE id=a.session_id;
    SELECT * INTO i FROM agent_action_dispatch_intents WHERE attempt_id=p_attempt_id;
    SELECT * INTO cmd FROM agent_session_commands WHERE id=r.command_id;
    IF a.status NOT IN ('queued','running') OR t.status NOT IN ('claimed','dispatching')
       OR r.status IN ('completed','failed','cancelled')
       OR t.lease_expires_at IS NULL OR t.lease_expires_at <= clock_timestamp()
       OR i.recovery_mode <> 'idempotent_replay' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_ATTEMPT_NOT_ACTIVE' USING ERRCODE='55000';
    END IF;
    IF a.org_id IS DISTINCT FROM t.org_id OR a.user_id IS DISTINCT FROM t.user_id
       OR a.org_id IS DISTINCT FROM r.org_id OR a.user_id IS DISTINCT FROM r.user_id
       OR s.org_id IS DISTINCT FROM a.org_id OR s.user_id IS DISTINCT FROM a.user_id
       OR s.conversation_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_SCOPE_INVALID' USING ERRCODE='42501';
    END IF;
    IF r.context_receipt->>'through_message_id' IS NULL
       OR r.context_receipt->>'base_context_revision' IS DISTINCT FROM
          ('message:' || (r.context_receipt->>'through_message_id'))
       OR r.context_receipt->>'session_id' IS DISTINCT FROM s.id::TEXT
       OR r.context_receipt->>'conversation_id' IS DISTINCT FROM s.conversation_id::TEXT
       OR r.config_snapshot IS DISTINCT FROM cmd.payload->'run_envelope'->'config_snapshot'
       OR r.capability_snapshot IS DISTINCT FROM cmd.payload->'run_envelope'->'capability_snapshot'
       OR cmd.payload->'run_envelope'->>'schema_revision' IS NULL
       OR cmd.payload->'run_envelope'->>'schema_revision' NOT IN ('2','3')
       OR cmd.payload->'run_envelope'->'context_receipt' IS DISTINCT FROM r.context_receipt
       OR r.config_snapshot->>'base_context_revision' IS DISTINCT FROM r.context_receipt->>'base_context_revision'
       OR r.config_snapshot->>'through_message_id' IS DISTINCT FROM r.context_receipt->>'through_message_id'
       OR cmd.payload->>'release_revision' IS DISTINCT FROM r.config_snapshot->>'release_revision' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_CONTEXT_INVALID' USING ERRCODE='42501';
    END IF;
    SELECT m.* INTO anchor FROM messages m
      JOIN conversations v ON v.id=m.conversation_id
     WHERE m.id=(r.context_receipt->>'through_message_id')::UUID
       AND m.conversation_id=s.conversation_id
       AND m.org_id IS NOT DISTINCT FROM s.org_id
       AND ((s.scope_kind='user' AND v.user_id=s.user_id)
         OR (s.scope_kind='channel' AND v.scope_type='channel'
             AND v.scope_id=s.scope_id));
    IF NOT FOUND OR anchor.context_revision IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_ANCHOR_INVALID' USING ERRCODE='42501';
    END IF;
    fence:=anchor.context_revision;
    RETURN jsonb_build_object(
      'conversation_id', s.conversation_id, 'org_id', a.org_id,
      'user_id', a.user_id, 'scope_kind', s.scope_kind,
      'scope_id', s.scope_id, 'context_revision', fence,
      'through_message_id', anchor.id);
END $$;

REVOKE ALL ON FUNCTION get_agent_runtime_model_context_v2(UUID,TEXT,UUID),
 _agent_runtime_read_context(UUID,UUID,UUID,TEXT,TEXT,INTEGER)
 FROM PUBLIC,everydayai,everydayai_runtime,everydayai_wecom_runtime,
 everydayai_worker,everydayai_sync,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker,
 everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION get_agent_runtime_model_context_v2(UUID,TEXT,UUID),
 _agent_runtime_read_context(UUID,UUID,UUID,TEXT,TEXT,INTEGER)
 TO everydayai_agent_runtime_worker;

RESET ROLE;
