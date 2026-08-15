-- 228.08q: converge production ingress and Action dispatch onto one owner.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE agent_policy_receipts ADD COLUMN IF NOT EXISTS attempt_id UUID
 REFERENCES agent_action_attempts(id) ON DELETE RESTRICT;
DO $$
DECLARE constraint_name NAME;
BEGIN
    SELECT item.conname INTO constraint_name FROM pg_constraint item
     WHERE item.conrelid='agent_policy_receipts'::regclass AND item.contype='u'
       AND pg_get_constraintdef(item.oid)='UNIQUE (action_id, arguments_hash, executor_type, executor_revision, policy_revision)';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE agent_policy_receipts DROP CONSTRAINT %I',constraint_name);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_policy_receipts_action_policy ON
 agent_policy_receipts(action_id,arguments_hash,executor_type,executor_revision,
 policy_revision) WHERE attempt_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_policy_receipts_safe_attempt
 ON agent_policy_receipts(attempt_id) WHERE attempt_id IS NOT NULL;
CREATE FUNCTION _submit_runtime_ingress_core_v1(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,p_created_by_user_id UUID,
 p_agent_definition_id TEXT,p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,p_command_type TEXT,
 p_idempotency_key TEXT,p_channel TEXT,p_through_message_id UUID,p_base_context_revision TEXT,
 p_effective_toolset_revision TEXT,p_effective_toolset_hash TEXT,p_config_snapshot JSONB,
 p_capability_snapshot JSONB,p_release_revision TEXT,p_payload JSONB) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
 ctl agent_runtime_control%ROWTYPE; s JSONB; sid UUID; d agent_runtime_definition_facts%ROWTYPE;
 c agent_runtime_catalog_facts%ROWTYPE; t agent_runtime_effective_toolset_facts%ROWTYPE;
 v_gate TEXT; command_result JSONB; config JSONB; capabilities JSONB; kill JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 kill:=_agent_runtime_ingress_kill_epoch_context(p_org_id);
 IF kill->>'outcome'<>'allowed' THEN RETURN jsonb_build_object('outcome','ingress_disabled',
  'error_code',kill->>'error_code','ingress_contract','required-v1'); END IF;
 IF p_scope_kind NOT IN('user','channel') OR p_channel NOT IN('web','wecom') OR p_through_message_id IS NULL
    OR p_base_context_revision IS DISTINCT FROM 'message:'||p_through_message_id::TEXT
    OR NULLIF(btrim(p_scope_id),'') IS NULL OR NULLIF(btrim(p_idempotency_key),'') IS NULL
    OR NULLIF(btrim(p_release_revision),'') IS NULL OR p_command_type IS DISTINCT FROM 'submit_input'
    OR jsonb_typeof(COALESCE(p_payload,'{}'::JSONB)) IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'RUNTIME_REQUIRED_INGRESS_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO ctl FROM agent_runtime_control WHERE singleton FOR SHARE;
 IF NOT ctl.ingress_enabled THEN RETURN jsonb_build_object(
  'outcome','ingress_disabled','ingress_contract','required-v1'); END IF;
 IF NOT EXISTS(SELECT 1 FROM messages message WHERE message.id=p_through_message_id
   AND message.conversation_id=p_conversation_id AND message.org_id IS NOT DISTINCT FROM p_org_id) THEN
  RAISE EXCEPTION 'RUNTIME_CONTEXT_ANCHOR_MISSING';
 END IF;
 s:=ensure_agent_runtime_session(
  p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
  p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision);
 IF s->>'outcome' NOT IN('created','already_exists') THEN RETURN s; END IF;
 sid:=(s->>'entity_id')::UUID;
 SELECT * INTO d FROM agent_runtime_definition_facts WHERE agent_key=p_agent_definition_id
  AND definition_revision=p_agent_definition_revision AND recoverable AND enabled_for_new_ingress;
 SELECT * INTO c FROM agent_runtime_catalog_facts WHERE catalog_revision=d.catalog_revision
  AND recoverable AND enabled_for_new_ingress;
 IF d.agent_key IS NULL OR c.catalog_revision IS NULL OR d.definition_hash IS DISTINCT FROM p_agent_definition_hash
    OR p_effective_toolset_revision IS DISTINCT FROM d.catalog_revision THEN RAISE EXCEPTION 'RUNTIME_VERSION_FACT_NOT_ENABLED'; END IF;
 v_gate:='disabled';
 IF ctl.non_safe_actions_enabled AND ctl.code_execute_enabled AND ctl.tool_confirmation_enabled
    AND EXISTS(SELECT 1 FROM agent_runtime_worker_heartbeats heartbeat WHERE heartbeat.process_role='sandbox'
        AND heartbeat.ready AND NOT heartbeat.draining
        AND heartbeat.observed_at>clock_timestamp()-interval '30 seconds') THEN
  v_gate:='enabled';
 END IF;
 SELECT * INTO t FROM agent_runtime_effective_toolset_facts
  WHERE agent_key=d.agent_key AND definition_revision=d.definition_revision
    AND catalog_revision=d.catalog_revision AND scope_kind=p_scope_kind
    AND channel=p_channel AND gate_state=v_gate
    AND recoverable AND enabled_for_new_ingress;
 IF t.effective_toolset_hash IS NULL THEN RAISE EXCEPTION 'RUNTIME_EFFECTIVE_TOOLSET_FACT_MISSING'; END IF;
 config:=COALESCE(p_config_snapshot,'{}'::JSONB)||jsonb_build_object('base_context_revision',p_base_context_revision,
  'through_message_id',p_through_message_id,'agent_definition_revision',p_agent_definition_revision,
  'agent_definition_hash',p_agent_definition_hash,
  'tool_catalog_revision',d.catalog_revision,'tool_catalog_hash',c.catalog_hash,
  'effective_toolset_revision',d.catalog_revision,'effective_toolset_hash',t.effective_toolset_hash,
  'release_revision',p_release_revision,
  'config_snapshot_hash',md5(COALESCE(p_config_snapshot,'{}'::JSONB)::TEXT));
 capabilities:=COALESCE(p_capability_snapshot,'{}'::JSONB)||jsonb_build_object('channel',p_channel,
  'agent_definition_id',p_agent_definition_id,'agent_definition_revision',p_agent_definition_revision,
  'agent_definition_hash',p_agent_definition_hash,
  'tool_catalog_revision',d.catalog_revision,'tool_catalog_hash',c.catalog_hash,
  'effective_toolset_revision',d.catalog_revision,'effective_toolset_hash',t.effective_toolset_hash,'gate_state',v_gate,
  'capability_snapshot_hash',md5(COALESCE(p_capability_snapshot,'{}'::JSONB)::TEXT));
 command_result:=submit_session_command(sid,p_command_type,p_idempotency_key,
  COALESCE(p_payload,'{}'::JSONB)||jsonb_build_object('run_envelope',jsonb_build_object(
    'schema_revision',3,'run_kind','user','context_receipt',jsonb_build_object(
     'base_context_revision',p_base_context_revision,'through_message_id',p_through_message_id,
     'session_id',sid,'conversation_id',p_conversation_id),
    'config_snapshot',config,'capability_snapshot',capabilities,
    'request_identity',jsonb_build_object(
     'session_id',sid,'idempotency_key',p_idempotency_key,'channel',p_channel,
     'conversation_id',p_conversation_id,'user_id',p_user_id,'org_id',p_org_id,'scope_kind',p_scope_kind,
     'scope_id',p_scope_id,'through_message_id',p_through_message_id,
     'base_context_revision',p_base_context_revision,'agent_definition_id',p_agent_definition_id,
     'agent_definition_revision',p_agent_definition_revision,'agent_definition_hash',p_agent_definition_hash,
     'catalog_revision',d.catalog_revision,
     'effective_toolset_hash',t.effective_toolset_hash)),
   'release_revision',p_release_revision));
 RETURN command_result||jsonb_build_object('session_id',sid,'ingress_contract','required-v1',
  'effective_toolset_revision',d.catalog_revision,
  'effective_toolset_hash',t.effective_toolset_hash,'gate_state',v_gate);
END $$;
CREATE FUNCTION submit_runtime_ingress_required_v1(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,p_created_by_user_id UUID,
 p_agent_definition_id TEXT,p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,p_command_type TEXT,
 p_idempotency_key TEXT,p_channel TEXT,p_through_message_id UUID,p_base_context_revision TEXT,
 p_effective_toolset_revision TEXT,p_effective_toolset_hash TEXT,p_config_snapshot JSONB,
 p_capability_snapshot JSONB,p_release_revision TEXT,p_payload JSONB,p_task_id UUID,p_client_task_id TEXT,
 p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,p_request_id TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE result JSONB; task tasks%ROWTYPE;
 session agent_runtime_sessions%ROWTYPE; command agent_session_commands%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 IF p_request_id IS DISTINCT FROM p_idempotency_key OR p_channel<>'web'
    OR p_scope_kind<>'user' OR p_scope_id<>p_user_id::TEXT
    OR p_created_by_user_id IS DISTINCT FROM p_user_id
    OR p_command_type<>'submit_input' OR p_payload->>'channel'<>'web'
    OR p_payload->>'task_id' IS DISTINCT FROM p_task_id::TEXT
    OR p_payload->>'client_task_id' IS DISTINCT FROM p_client_task_id
    OR p_payload->>'input_message_id' IS DISTINCT FROM p_input_message_id::TEXT
    OR p_payload->>'output_message_id' IS DISTINCT FROM p_output_message_id::TEXT
    OR p_payload->>'turn_id' IS DISTINCT FROM p_turn_id::TEXT
    OR p_payload->>'request_id' IS DISTINCT FROM p_request_id THEN
  RAISE EXCEPTION 'RUNTIME_WEB_OWNER_REQUEST_BINDING_MISMATCH' USING ERRCODE='42501';
 END IF;
 task:=_agent_runtime_validate_web_task_binding(
  p_task_id,p_conversation_id,p_user_id,p_org_id,p_input_message_id,
  p_output_message_id,p_turn_id,p_through_message_id,
  p_base_context_revision,p_client_task_id);
 result:=_submit_runtime_ingress_core_v1(
  p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
  p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision,
  p_agent_definition_hash,p_command_type,p_idempotency_key,p_channel,
  p_through_message_id,p_base_context_revision,p_effective_toolset_revision,
  p_effective_toolset_hash,p_config_snapshot,p_capability_snapshot,
  p_release_revision,p_payload);
 IF result->>'outcome' NOT IN('created','already_exists') THEN
  IF NOT(task.delivery_context @>
    '{"actor":false,"runtime":false,"runtime_pending":true}'::JSONB) THEN
   RAISE EXCEPTION 'RUNTIME_REQUIRED_TASK_OWNER_STATE_MISMATCH' USING ERRCODE='55000';
  END IF;
  UPDATE tasks SET delivery_context=task.delivery_context||jsonb_build_object(
    'actor',FALSE,'runtime',TRUE,'runtime_pending',FALSE,
    'runtime_rejected',TRUE,'runtime_rejection_code',
    COALESCE(result->>'outcome','unknown')) WHERE id=p_task_id;
  RETURN result||jsonb_build_object(
    'outcome','runtime_required_unavailable','runtime_owned',FALSE);
 END IF;
 SELECT * INTO session FROM agent_runtime_sessions
  WHERE id=(result->>'session_id')::UUID
    AND conversation_id=p_conversation_id AND user_id=p_user_id
    AND org_id IS NOT DISTINCT FROM p_org_id;
 SELECT * INTO command FROM agent_session_commands
  WHERE id=(result->>'entity_id')::UUID AND session_id=session.id
    AND command_type='submit_input' AND idempotency_key=p_idempotency_key
    AND org_id IS NOT DISTINCT FROM p_org_id AND user_id=p_user_id;
 IF session.id IS NULL OR command.id IS NULL THEN
  RAISE EXCEPTION 'RUNTIME_WEB_OWNER_COMMAND_BINDING_MISMATCH' USING ERRCODE='42501';
 END IF;
 IF task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB THEN
  IF task.delivery_context->>'runtime_session_id' IS DISTINCT FROM session.id::TEXT
     OR task.delivery_context->>'runtime_command_id' IS DISTINCT FROM command.id::TEXT THEN
   RAISE EXCEPTION 'RUNTIME_WEB_OWNER_REPLAY_BINDING_MISMATCH' USING ERRCODE='55000';
  END IF;
  RETURN result||jsonb_build_object(
   'outcome','already_runtime_owned','runtime_owned',TRUE);
 END IF;
 IF NOT(task.delivery_context @>
   '{"actor":false,"runtime":false,"runtime_pending":true}'::JSONB) THEN
  RAISE EXCEPTION 'RUNTIME_WEB_OWNER_MARK_STATE_MISMATCH' USING ERRCODE='55000';
 END IF;
 UPDATE tasks SET delivery_context=task.delivery_context||jsonb_build_object(
  'actor',FALSE,'runtime',TRUE,'runtime_pending',FALSE,
  'runtime_session_id',session.id,'runtime_command_id',command.id)
  WHERE id=p_task_id;
 RETURN result||jsonb_build_object(
  'outcome','marked','task_id',p_task_id,'runtime_owned',TRUE);
END $$;
CREATE FUNCTION enqueue_wecom_runtime_turn_required_v1(
 p_task_data JSONB,p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,p_input_content JSONB,
 p_delivery_context JSONB,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,
 p_effective_toolset_revision TEXT,p_effective_toolset_hash TEXT,p_release_revision TEXT,p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE e JSONB; result JSONB; task tasks%ROWTYPE; conversation conversations%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 e:=enqueue_wecom_generation_turn_v2(
  p_task_data,p_input_message_id,p_output_message_id,p_turn_id,
  p_input_content,p_delivery_context);
 SELECT * INTO task FROM tasks WHERE id=(e->>'task_id')::UUID FOR UPDATE;
 SELECT * INTO conversation FROM conversations WHERE id=task.conversation_id;
 IF task.id IS NULL OR conversation.id IS NULL THEN
  RAISE EXCEPTION 'WECOM_RUNTIME_TASK_BINDING_MISSING' USING ERRCODE='42501';
 END IF;
 result:=_submit_runtime_ingress_core_v1(
  task.conversation_id,task.org_id,task.user_id,conversation.scope_type,
  conversation.scope_id,task.user_id,p_agent_definition_id,
  p_agent_definition_revision,p_agent_definition_hash,'submit_input',
  p_idempotency_key,'wecom',p_input_message_id,
  'message:'||p_input_message_id::TEXT,p_effective_toolset_revision,
  p_effective_toolset_hash,'{}'::JSONB,
  jsonb_build_object('requested_groups',jsonb_build_array('code')),
  p_release_revision,jsonb_build_object(
   'schema_revision',3,'channel','wecom','task_id',task.id,
   'input_message_id',p_input_message_id,'output_message_id',p_output_message_id,
   'turn_id',p_turn_id,'content',p_input_content,
   'delivery_context',p_delivery_context||'{"actor":false,"runtime":true}'::JSONB));
 IF result->>'outcome' IN('created','already_exists') THEN
  UPDATE tasks SET delivery_context=delivery_context||jsonb_build_object(
   'actor',FALSE,'runtime',TRUE,'runtime_pending',FALSE,
   'runtime_session_id',result->>'session_id',
   'runtime_command_id',result->>'entity_id') WHERE id=task.id;
  RETURN e||result||jsonb_build_object('runtime_owned',TRUE);
 END IF;
 UPDATE tasks SET status='failed',error_message='runtime_required_unavailable',
  completed_at=clock_timestamp(),delivery_context=delivery_context||jsonb_build_object(
   'actor',FALSE,'runtime',TRUE,'runtime_pending',FALSE,
   'runtime_rejected',TRUE,'runtime_rejection_code',result->>'outcome')
  WHERE id=task.id;
 UPDATE messages SET status='failed',content=jsonb_build_array(jsonb_build_object(
  'type','text','text','生成服务暂未就绪，请稍后重试'))::TEXT
  WHERE id=p_output_message_id AND conversation_id=task.conversation_id;
 RETURN e||result||jsonb_build_object(
  'outcome','runtime_required_unavailable','runtime_owned',FALSE);
END $$;
CREATE FUNCTION _record_safe_attempt_policy_receipt_v1(
 p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,p_request_hash TEXT,
 p_executor_type TEXT,p_executor_revision INTEGER,p_policy_revision TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE attempt agent_action_attempts%ROWTYPE; action agent_actions%ROWTYPE;
 run agent_runs%ROWTYPE; session agent_runtime_sessions%ROWTYPE;
 toolset agent_runtime_effective_toolset_facts%ROWTYPE; tool JSONB;
 receipt agent_policy_receipts%ROWTYPE; fence JSONB; receipt_hash TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_request_hash!~'^[0-9a-f]{64}$' OR p_executor_revision<1
    OR NULLIF(btrim(p_executor_type),'') IS NULL
    OR NULLIF(btrim(p_policy_revision),'') IS NULL THEN
  RAISE EXCEPTION 'AGENT_SAFE_ATTEMPT_POLICY_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id;
 IF attempt.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO session FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
 SELECT * INTO run FROM agent_runs WHERE id=attempt.run_id FOR UPDATE;
 SELECT * INTO action FROM agent_actions WHERE id=attempt.action_id FOR UPDATE;
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
 IF attempt.execution_token IS DISTINCT FROM p_execution_token THEN RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
 IF attempt.state_version IS DISTINCT FROM p_expected_attempt_version OR attempt.status<>'claimed'
    OR action.status<>'running' OR attempt.lease_expires_at<=clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','stale_version'); END IF;
 IF attempt.request_hash IS DISTINCT FROM p_request_hash OR action.request_hash IS DISTINCT FROM p_request_hash THEN
  RETURN jsonb_build_object('outcome','request_hash_conflict'); END IF;
 IF action.org_id IS NULL OR action.org_id IS DISTINCT FROM run.org_id OR action.org_id IS DISTINCT FROM session.org_id
    OR attempt.org_id IS DISTINCT FROM action.org_id OR attempt.run_id IS DISTINCT FROM action.run_id THEN
  RETURN jsonb_build_object('outcome','scope_mismatch'); END IF;
 SELECT * INTO receipt FROM agent_policy_receipts WHERE attempt_id=attempt.id FOR UPDATE;
 IF receipt.id IS NOT NULL THEN
  IF receipt.action_id IS DISTINCT FROM action.id
     OR receipt.arguments_hash IS DISTINCT FROM action.arguments_hash
     OR receipt.executor_type IS DISTINCT FROM btrim(p_executor_type)
     OR receipt.executor_revision IS DISTINCT FROM p_executor_revision
     OR receipt.policy_revision IS DISTINCT FROM btrim(p_policy_revision) THEN
   RETURN jsonb_build_object('outcome','receipt_conflict');
  END IF;
  RETURN jsonb_build_object('outcome','already_activated','policy_receipt_id',receipt.id);
 END IF;
 IF action.policy_decision<>'preauthorized' OR action.policy_snapshot->>'safety_level'<>'safe'
    OR action.policy_snapshot->>'side_effect'<>'none' OR action.policy_snapshot->>'authorization_requirement'<>'none'
    OR action.policy_revision IS DISTINCT FROM btrim(p_policy_revision) THEN
  RETURN jsonb_build_object('outcome','safe_policy_required');
 END IF;
 SELECT * INTO toolset FROM agent_runtime_effective_toolset_facts fact
  WHERE fact.effective_toolset_hash=run.capability_snapshot->>'effective_toolset_hash'
    AND fact.catalog_revision=run.capability_snapshot->>'effective_toolset_revision'
    AND fact.scope_kind=session.scope_kind
    AND fact.channel=run.capability_snapshot->>'channel' AND fact.recoverable;
 IF toolset.effective_toolset_hash IS NULL THEN RETURN jsonb_build_object('outcome','toolset_mismatch'); END IF;
 SELECT item.value INTO tool FROM jsonb_array_elements(toolset.toolset_document->'tools') item(value)
  WHERE item.value->>'canonical_name'=action.tool_name;
 IF tool IS NULL OR tool->>'safety_level'<>'safe' OR tool->>'side_effect'<>'none'
    OR tool->>'authorization_requirement'<>'none' OR tool->>'executor_type' IS DISTINCT FROM btrim(p_executor_type)
    OR (tool->>'executor_revision')::INTEGER IS DISTINCT FROM p_executor_revision
    OR tool->>'schema_hash' IS DISTINCT FROM action.policy_snapshot->>'schema_hash'
    OR toolset.catalog_revision IS DISTINCT FROM action.policy_snapshot->>'catalog_revision'
    OR toolset.effective_toolset_hash IS DISTINCT FROM action.policy_snapshot->>'effective_toolset_hash'
    OR action.policy_snapshot->'capability_requirements' IS DISTINCT FROM tool->'capability_requirements' THEN
  RETURN jsonb_build_object('outcome','toolset_mismatch'); END IF;
 fence:=_agent_runtime_kill_epoch_context(
  p_attempt_id,p_execution_token,p_request_hash,p_expected_attempt_version,'dispatch');
 IF fence->>'outcome'<>'allowed' THEN RETURN fence; END IF;
 receipt_hash:=encode(digest(convert_to(jsonb_build_object(
  'action_id',action.id,'attempt_id',attempt.id,'execution_token',p_execution_token,
  'request_hash',p_request_hash,'executor_type',btrim(p_executor_type),
  'executor_revision',p_executor_revision,'policy_revision',btrim(p_policy_revision),
  'effective_toolset_hash',toolset.effective_toolset_hash,
  'tenant_kill_epoch',fence->'tenant_kill_epoch',
  'capability_kill_epoch',fence->'capability_kill_epoch')::TEXT,'UTF8'),
  'sha256'),'hex');
 INSERT INTO agent_policy_receipts(
  action_id,attempt_id,session_id,run_id,org_id,user_id,decision,arguments_hash,
  executor_type,executor_revision,policy_revision,effective_scope,reason_codes,
  obligations,receipt_hash,expires_at)
 VALUES(action.id,attempt.id,action.session_id,action.run_id,action.org_id,
  action.user_id,'allow',action.arguments_hash,btrim(p_executor_type),
  p_executor_revision,btrim(p_policy_revision),jsonb_build_object(
   'org_id',action.org_id,'run_id',action.run_id,'action_id',action.id,
   'attempt_id',attempt.id,'request_hash',p_request_hash,
   'effective_toolset_hash',toolset.effective_toolset_hash),
  ARRAY['NO_AUTHORIZATION_REQUIRED','SAFE_LOCAL_READ'],ARRAY['audit'],
  receipt_hash,clock_timestamp()+interval '5 minutes')
 RETURNING * INTO receipt;
 RETURN jsonb_build_object('outcome','activated','policy_receipt_id',receipt.id);
END $$;
CREATE FUNCTION gate_agent_action_dispatch_final_v1(
 p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,p_request_hash TEXT,
 p_policy_receipt_id UUID,p_executor_type TEXT,p_executor_revision INTEGER,p_policy_revision TEXT,
 p_recovery_mode TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE fence JSONB; result JSONB; activation JSONB;
 control agent_runtime_control%ROWTYPE; action agent_actions%ROWTYPE;
 receipt_id UUID:=p_policy_receipt_id;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 fence:=_agent_runtime_kill_epoch_context(
  p_attempt_id,p_execution_token,p_request_hash,p_expected_attempt_version,'dispatch');
 IF fence->>'outcome'<>'allowed' THEN RETURN fence; END IF;
 SELECT action_row.* INTO action FROM agent_actions action_row JOIN agent_action_attempts attempt
  ON attempt.action_id=action_row.id WHERE attempt.id=p_attempt_id;
 SELECT * INTO control FROM agent_runtime_control WHERE singleton FOR SHARE;
 IF action.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF NOT control.action_dispatch_enabled
   OR(action.tool_name='code_execute' AND(
     NOT control.code_execute_enabled OR NOT control.non_safe_actions_enabled
     OR NOT control.tool_confirmation_enabled))
   OR(action.tool_name<>'code_execute'
      AND action.policy_snapshot->>'safety_level'='safe'
      AND NOT control.safe_actions_enabled)
   OR(action.tool_name<>'code_execute'
      AND action.policy_snapshot->>'safety_level'<>'safe'
      AND(NOT control.non_safe_actions_enabled
          OR NOT control.tool_confirmation_enabled))
   OR action.policy_snapshot->>'safety_level' IS NULL THEN
  RETURN _reject_agent_action_before_dispatch_gate(
   action.id,p_attempt_id,'action_dispatch_disabled','action_not_dispatchable');
 END IF;
 IF receipt_id IS NULL THEN
  activation:=_record_safe_attempt_policy_receipt_v1(
   p_attempt_id,p_execution_token,p_expected_attempt_version,p_request_hash,
   p_executor_type,p_executor_revision,p_policy_revision);
  IF activation->>'outcome' NOT IN('activated','already_activated') THEN
   RETURN _reject_agent_action_before_dispatch_gate(
    action.id,p_attempt_id,'safe_policy_'||COALESCE(activation->>'outcome','invalid'),
    'action_not_dispatchable');
  END IF;
  receipt_id:=(activation->>'policy_receipt_id')::UUID;
 END IF;
 result:=_gate_agent_action_dispatch_220_24(
  p_attempt_id,p_execution_token,p_expected_attempt_version,p_request_hash,
  receipt_id,p_executor_type,p_executor_revision,p_policy_revision,p_recovery_mode);
 RETURN result;
END $$;
CREATE FUNCTION _recover_expired_agent_action_claims_v1(
 p_worker_id TEXT,p_max_attempts INTEGER DEFAULT 3) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE candidate RECORD; attempt agent_action_attempts%ROWTYPE;
 action agent_actions%ROWTYPE; recovered INTEGER:=0; unknown_count INTEGER:=0;
 result JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF NULLIF(btrim(p_worker_id),'') IS NULL OR p_max_attempts NOT BETWEEN 1 AND 20 THEN
  RAISE EXCEPTION 'AGENT_ACTION_RECOVERY_SCAN_INVALID' USING ERRCODE='22023';
 END IF;
 FOR candidate IN SELECT item.id,item.state_version,item.status
  FROM agent_action_attempts item
  JOIN agent_actions action_row ON action_row.id=item.action_id
  JOIN agent_runs run_row ON run_row.id=item.run_id
  WHERE item.status IN('claimed','dispatching')
    AND item.lease_expires_at<=clock_timestamp()
    AND action_row.status='running'
    AND run_row.status IN('running','waiting_actions')
  ORDER BY item.lease_expires_at,item.id LIMIT 100
 LOOP
  IF candidate.status='dispatching' THEN
   result:=recover_expired_agent_action_attempt(candidate.id,candidate.state_version,p_worker_id,120);
   IF result->>'outcome'='unknown' THEN unknown_count:=unknown_count+1; END IF;
   CONTINUE;
  END IF;
  SELECT * INTO attempt FROM agent_action_attempts WHERE id=candidate.id;
  IF attempt.id IS NULL THEN CONTINUE; END IF;
  PERFORM 1 FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
  PERFORM 1 FROM agent_runs WHERE id=attempt.run_id FOR UPDATE;
  SELECT * INTO action FROM agent_actions WHERE id=attempt.action_id FOR UPDATE;
  SELECT * INTO attempt FROM agent_action_attempts WHERE id=candidate.id FOR UPDATE;
  IF attempt.status<>'claimed' OR attempt.state_version<>candidate.state_version
     OR attempt.lease_expires_at>clock_timestamp() OR action.status<>'running' THEN CONTINUE; END IF;
  IF attempt.attempt_number>=p_max_attempts THEN
   PERFORM _reject_agent_action_before_dispatch_gate(
    action.id,attempt.id,'predispatch_attempts_exhausted','action_not_dispatchable');
   recovered:=recovered+1;
   CONTINUE;
  END IF;
  UPDATE agent_action_attempts SET status='failed',execution_token=NULL,
   lease_expires_at=NULL,state_version=state_version+1,ended_at=clock_timestamp(),
   updated_at=clock_timestamp() WHERE id=attempt.id;
  UPDATE agent_actions SET status='queued',state_version=state_version+1,
   updated_at=clock_timestamp() WHERE id=action.id;
  PERFORM append_agent_runtime_event(
   action.session_id,'action.retry_scheduled',action.run_id,action.model_step_id,
   action.id,'system',session_user,jsonb_build_object(
    'action_id',action.id,'error_code','predispatch_lease_expired'),
   ARRAY['audit']::TEXT[]);
  recovered:=recovered+1;
 END LOOP;
 RETURN jsonb_build_object(
  'outcome','recovered','requeued_or_closed',recovered,'unknown',unknown_count);
END $$;
CREATE FUNCTION claim_agent_action_dispatch_final_v1(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE existing JSONB; result JSONB; org RECORD;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 existing:=get_agent_action_snapshot_batch(p_worker_id,p_claim_request_id);
 IF existing->>'outcome'='found' THEN
  RETURN jsonb_build_object('outcome','claimed','snapshots',existing->'snapshots');
 END IF;
 PERFORM _recover_expired_agent_action_claims_v1(p_worker_id,3);
 FOR org IN SELECT DISTINCT action.org_id
  FROM agent_actions action JOIN agent_runs run ON run.id=action.run_id
  WHERE action.status='queued' AND run.status IN('running','waiting_actions')
    AND action.org_id IS NOT NULL
 LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended(
   'agent-runtime-kill-gate:'||org.org_id::TEXT||':tenant:tenant',0));
 END LOOP;
 IF EXISTS(SELECT 1 FROM agent_actions action
  JOIN agent_runs run ON run.id=action.run_id
  JOIN agent_runtime_tenant_gate_controls gate
    ON gate.org_id=action.org_id AND gate.gate_scope='tenant'
   AND gate.scope_key='tenant'
  WHERE action.status='queued' AND run.status IN('running','waiting_actions')
    AND gate.claim_blocked) THEN
  RETURN jsonb_build_object(
   'outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
 END IF;
 result:=claim_ready_agent_action_snapshots(
  p_worker_id,p_claim_request_id,p_batch_size,p_lease_seconds);
 IF result->>'outcome'='claimed' THEN
  FOR org IN SELECT attempt.id FROM agent_action_attempts attempt
   WHERE attempt.claim_request_id=p_claim_request_id
  LOOP
   PERFORM _agent_runtime_record_attempt_fence(org.id);
  END LOOP;
 END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION
 _submit_runtime_ingress_core_v1(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB),
 submit_runtime_ingress_required_v1(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT),
 enqueue_wecom_runtime_turn_required_v1(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
 _record_safe_attempt_policy_receipt_v1(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT),
 gate_agent_action_dispatch_final_v1(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),
 _recover_expired_agent_action_claims_v1(TEXT,INTEGER),claim_agent_action_dispatch_final_v1(TEXT,TEXT,INTEGER,INTEGER)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,
 everydayai_agent_runtime_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION submit_runtime_ingress_required_v1(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT) TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION enqueue_wecom_runtime_turn_required_v1(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT) TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION
 gate_agent_action_dispatch_final_v1(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),
 claim_agent_action_dispatch_final_v1(TEXT,TEXT,INTEGER,INTEGER) TO everydayai_agent_runtime_worker;
REVOKE EXECUTE ON FUNCTION
 get_agent_runtime_ingress_capability(),
 runtime_submit_ingress_v4(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB),
 runtime_submit_ingress_v6_required(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT),
 runtime_submit_ingress_v5_owner_transition(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT) FROM PUBLIC,everydayai_runtime;
REVOKE EXECUTE ON FUNCTION
 get_agent_runtime_ingress_capability(),
 enqueue_wecom_runtime_turn_v3(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT),
 enqueue_wecom_runtime_turn_v4(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
 enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
 enqueue_wecom_runtime_turn_v6(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT) FROM PUBLIC,everydayai_wecom_runtime;
REVOKE EXECUTE ON FUNCTION
 claim_ready_agent_action_snapshots_v2(TEXT,TEXT,INTEGER,INTEGER),
 claim_ready_agent_actions_v2(TEXT,TEXT,INTEGER,INTEGER),
 gate_agent_action_dispatch_v2(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),
 activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT) FROM PUBLIC,everydayai_agent_runtime_worker;
REVOKE EXECUTE ON FUNCTION activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT) FROM PUBLIC,everydayai_authorization_worker;
REVOKE EXECUTE ON FUNCTION
 set_agent_runtime_org_rollout(UUID,UUID,BOOLEAN,TEXT),
 set_agent_runtime_rollout_subject(TEXT,TEXT,TEXT,BOOLEAN,JSONB) FROM PUBLIC,everydayai_runtime_admin;

RESET ROLE;
