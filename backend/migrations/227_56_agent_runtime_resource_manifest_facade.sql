-- 227.56: Attempt-fenced read facade for the existing task attachment manifest.
-- No second attachment or workspace ownership model is introduced.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_agent_runtime_resource_manifest_v1(
 p_attempt_id UUID,p_worker_id TEXT,p_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 x agent_actions%ROWTYPE; a agent_action_attempts%ROWTYPE;
 c agent_session_commands%ROWTYPE; conversation conversations%ROWTYPE;
 task tasks%ROWTYPE; input_message messages%ROWTYPE;
 v_input_message_id UUID; v_task_id UUID; v_turn_id UUID;
 frozen_ref_count BIGINT:=0; web_part_count BIGINT:=0;
 assets JSONB:='[]'::JSONB; manifest_source TEXT:='input_message';
 workspace_owner_id TEXT; workspace_scope TEXT;
 actor_user_id UUID;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_attempt_id IS NULL OR NULLIF(btrim(p_worker_id),'') IS NULL
 OR p_execution_token IS NULL OR p_expected_attempt_version IS NULL
 OR p_expected_attempt_version<0
 OR COALESCE(p_request_hash,'')!~'^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_MANIFEST_INVALID'
   USING ERRCODE='22023';
 END IF;
 SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_MANIFEST_SCOPE_INVALID'
   USING ERRCODE='42501';
 END IF;
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=a.session_id FOR SHARE;
 SELECT * INTO r FROM agent_runs WHERE id=a.run_id FOR SHARE;
 SELECT * INTO x FROM agent_actions WHERE id=a.action_id FOR SHARE;
 SELECT * INTO c FROM agent_session_commands WHERE id=r.command_id FOR SHARE;
 SELECT * INTO conversation FROM conversations
  WHERE id=ss.conversation_id FOR SHARE;
 SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
 IF ss.id IS NULL OR r.id IS NULL OR x.id IS NULL OR c.id IS NULL
 OR conversation.id IS NULL
 OR r.session_id IS DISTINCT FROM ss.id OR x.session_id IS DISTINCT FROM ss.id
 OR a.session_id IS DISTINCT FROM ss.id OR x.run_id IS DISTINCT FROM r.id
 OR a.run_id IS DISTINCT FROM r.id OR a.action_id IS DISTINCT FROM x.id
 OR c.session_id IS DISTINCT FROM ss.id OR r.command_id IS DISTINCT FROM c.id
 OR r.org_id IS DISTINCT FROM ss.org_id OR x.org_id IS DISTINCT FROM ss.org_id
 OR a.org_id IS DISTINCT FROM ss.org_id
 OR r.user_id IS DISTINCT FROM ss.user_id OR x.user_id IS DISTINCT FROM ss.user_id
 OR a.user_id IS DISTINCT FROM ss.user_id
 OR conversation.org_id IS DISTINCT FROM ss.org_id
 OR r.context_receipt->>'base_context_revision' IS DISTINCT FROM
    'message:'||(r.context_receipt->>'through_message_id')
 OR r.context_receipt->>'session_id' IS DISTINCT FROM ss.id::TEXT
 OR r.context_receipt->>'conversation_id' IS DISTINCT FROM conversation.id::TEXT
 OR c.payload->'run_envelope'->'context_receipt' IS DISTINCT FROM r.context_receipt
 OR c.payload->'run_envelope'->'request_identity'->>'session_id'
    IS DISTINCT FROM ss.id::TEXT
 OR c.payload->'run_envelope'->'request_identity'->>'conversation_id'
    IS DISTINCT FROM conversation.id::TEXT
 OR c.payload->'run_envelope'->'request_identity'->>'user_id'
    IS DISTINCT FROM ss.user_id::TEXT
 OR c.payload->'run_envelope'->'request_identity'->>'org_id'
    IS DISTINCT FROM ss.org_id::TEXT
 OR c.payload->'run_envelope'->'request_identity'->>'scope_kind'
    IS DISTINCT FROM ss.scope_kind
 OR c.payload->'run_envelope'->'request_identity'->>'scope_id'
    IS DISTINCT FROM ss.scope_id
 OR c.payload->'run_envelope'->'request_identity'->>'through_message_id'
    IS DISTINCT FROM r.context_receipt->>'through_message_id'
 OR x.tool_name<>'file_analyze'
 OR x.policy_decision NOT IN ('preauthorized','requires_authorization')
 OR r.status<>'running' OR a.status<>'dispatching'
 OR a.dispatch_phase<>'request_started'
 OR a.worker_id IS DISTINCT FROM btrim(p_worker_id)
 OR a.execution_token IS DISTINCT FROM p_execution_token
 OR a.request_hash IS DISTINCT FROM p_request_hash
 OR a.state_version<>p_expected_attempt_version
 OR r.lease_expires_at<=clock_timestamp()
 OR a.lease_expires_at<=clock_timestamp()
 OR NOT EXISTS(
  SELECT 1 FROM agent_action_dispatch_intents intent
  JOIN agent_policy_receipts receipt ON receipt.id=intent.policy_receipt_id
   WHERE intent.attempt_id=a.id AND intent.action_id=x.id
     AND intent.execution_token=p_execution_token
     AND intent.request_hash=p_request_hash
     AND intent.executor_type='runtime_artifact_job:file_analyze'
     AND intent.executor_revision=1
     AND intent.recovery_mode='idempotent_replay'
     AND receipt.action_id=x.id AND receipt.decision='allow'
     AND receipt.session_id=ss.id AND receipt.run_id=r.id
     AND receipt.org_id=ss.org_id
     AND receipt.user_id IS NOT DISTINCT FROM ss.user_id
     AND receipt.arguments_hash=x.arguments_hash
     AND receipt.executor_type=intent.executor_type
     AND receipt.executor_revision=intent.executor_revision
     AND intent.policy_revision=receipt.policy_revision
     AND receipt.policy_revision=x.policy_revision
     AND receipt.expires_at>clock_timestamp()
 ) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_MANIFEST_SCOPE_INVALID'
   USING ERRCODE='42501';
 END IF;
 actor_user_id:=CASE WHEN ss.scope_kind='channel'
  THEN ss.created_by_user_id ELSE ss.user_id END;
 IF actor_user_id IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_MANIFEST_SCOPE_INVALID'
   USING ERRCODE='42501';
 END IF;
 PERFORM _agent_runtime_assert_facts_epoch(
  a.id,p_execution_token,a.org_id,NULL::TEXT,NULL::TEXT,'new'::TEXT
 );
 v_input_message_id:=NULLIF(c.payload->>'input_message_id','')::UUID;
 v_task_id:=NULLIF(c.payload->>'task_id','')::UUID;
 v_turn_id:=NULLIF(c.payload->>'turn_id','')::UUID;
 IF v_input_message_id IS NULL
 OR r.context_receipt->>'through_message_id'
    IS DISTINCT FROM v_input_message_id::TEXT THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_INPUT_ANCHOR_INVALID'
   USING ERRCODE='42501';
 END IF;
 SELECT * INTO input_message FROM messages
  WHERE id=v_input_message_id AND conversation_id=conversation.id FOR SHARE;
 IF input_message.id IS NULL OR input_message.role<>'user' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_INPUT_ANCHOR_INVALID'
   USING ERRCODE='42501';
 END IF;
 IF v_task_id IS NOT NULL THEN
  SELECT * INTO task FROM tasks WHERE id=v_task_id FOR SHARE;
 IF task.id IS NULL OR task.conversation_id IS DISTINCT FROM conversation.id
  OR task.user_id IS DISTINCT FROM actor_user_id
  OR task.org_id IS DISTINCT FROM ss.org_id
  OR task.input_message_id IS DISTINCT FROM v_input_message_id
  OR (v_turn_id IS NOT NULL AND task.turn_id IS DISTINCT FROM v_turn_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_TASK_SCOPE_INVALID'
    USING ERRCODE='42501';
  END IF;
  SELECT count(*) INTO frozen_ref_count FROM task_attachment_refs ref
   WHERE ref.task_id=task.id AND ref.input_message_id=v_input_message_id
    AND (v_turn_id IS NULL OR ref.turn_id=v_turn_id)
    AND ref.org_id=ss.org_id;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'asset_id',attachment.id,'attachment_set_id',ref.attachment_set_id,
    'name',attachment.canonical_name,
    'workspace_path',attachment.workspace_path,
    'mime_type',attachment.detected_mime_type,'size',attachment.size
   ) ORDER BY ref.created_at,attachment.id),'[]'::JSONB)
   INTO assets
   FROM task_attachment_refs ref
   JOIN conversation_attachment_refs attachment
    ON attachment.id=ref.attachment_id
   WHERE ref.task_id=task.id AND ref.input_message_id=v_input_message_id
    AND (v_turn_id IS NULL OR ref.turn_id=v_turn_id)
    AND ref.org_id=ss.org_id
    AND attachment.conversation_id=conversation.id
    AND attachment.org_id=ss.org_id
    AND attachment.attachment_set_id=ref.attachment_set_id
    AND attachment.status='ready'
    AND attachment.workspace_path=btrim(attachment.workspace_path)
    AND attachment.workspace_path!~'^/'
    AND position(chr(92) IN attachment.workspace_path)=0
    AND position('//' IN attachment.workspace_path)=0
    AND attachment.workspace_path!~'(^|/)\.{1,2}(/|$)';
  IF frozen_ref_count>0 AND jsonb_array_length(assets)<>frozen_ref_count THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE'
    USING ERRCODE='42501';
  END IF;
  IF frozen_ref_count>0 THEN
   manifest_source:='task_attachment_refs';
  END IF;
 END IF;
 IF frozen_ref_count=0 THEN
  IF ss.scope_kind<>'user' OR ss.user_id IS NULL THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE'
    USING ERRCODE='42501';
  END IF;
  BEGIN
   SELECT count(*) INTO web_part_count
    FROM jsonb_array_elements(input_message.content::JSONB) part
    WHERE part->>'type' IN ('file','image')
      AND NULLIF(btrim(part->>'workspace_path'),'') IS NOT NULL;
   SELECT COALESCE(jsonb_agg(jsonb_build_object(
     'asset_id',asset.id,'attachment_set_id',NULL,'name',asset.name,
     'workspace_path',asset.workspace_path,
     'mime_type',COALESCE(asset.mime_type,''),'size',asset.size
    ) ORDER BY source.ordinality),'[]'::JSONB)
    INTO assets
    FROM jsonb_array_elements(input_message.content::JSONB)
      WITH ORDINALITY AS source(part,ordinality)
    JOIN user_assets asset
      ON pg_input_is_valid(source.part->>'asset_id','uuid')
     AND asset.id=(source.part->>'asset_id')::UUID
     AND asset.org_id IS NOT DISTINCT FROM ss.org_id
     AND asset.storage_scope='user'
     AND asset.storage_owner_key=ss.user_id::TEXT
     AND asset.storage_provider='workspace'
     AND asset.workspace_path=source.part->>'workspace_path'
     AND asset.status='ready'
    WHERE source.part->>'type' IN ('file','image')
      AND asset.media_type=source.part->>'type'
      AND asset.workspace_path=btrim(asset.workspace_path)
      AND asset.workspace_path!~'^/'
      AND position(chr(92) IN asset.workspace_path)=0
      AND position('//' IN asset.workspace_path)=0
      AND asset.workspace_path!~'(^|/)\.{1,2}(/|$)';
  EXCEPTION WHEN invalid_text_representation THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_INPUT_CONTENT_INVALID'
    USING ERRCODE='22023';
  END;
  IF jsonb_array_length(assets)<>web_part_count THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE'
    USING ERRCODE='42501';
  END IF;
 END IF;
 IF conversation.scope_type='user' THEN
  IF conversation.user_id IS DISTINCT FROM ss.user_id
  OR ss.scope_kind<>'user' OR ss.scope_id IS DISTINCT FROM ss.user_id::TEXT THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_WORKSPACE_SCOPE_INVALID'
    USING ERRCODE='42501';
  END IF;
  workspace_scope:='user'; workspace_owner_id:=ss.user_id::TEXT;
 ELSIF conversation.scope_type='channel' THEN
  IF task.id IS NULL OR conversation.source<>'wecom'
  OR conversation.user_id IS NOT NULL
  OR ss.scope_kind<>'channel' OR ss.scope_id IS DISTINCT FROM conversation.scope_id
  OR task.delivery_context->>'channel'<>'wecom'
  OR task.delivery_context->>'chattype'<>'group'
  OR NULLIF(task.delivery_context->>'corp_id','') IS NULL
  OR NULLIF(task.delivery_context->>'chatid','') IS NULL
  OR conversation.scope_id IS DISTINCT FROM task.delivery_context->>'chatid' THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_WORKSPACE_SCOPE_INVALID'
    USING ERRCODE='42501';
  END IF;
  workspace_scope:='channel';
  workspace_owner_id:='channels/wecom/'||left(encode(digest(
   (task.delivery_context->>'corp_id')||':'||
   (task.delivery_context->>'chatid'),
   'sha256'),'hex'),24);
 ELSE
  RAISE EXCEPTION 'AGENT_RUNTIME_RESOURCE_WORKSPACE_SCOPE_UNSUPPORTED'
   USING ERRCODE='42501';
 END IF;
 RETURN jsonb_build_object(
  'org_id',ss.org_id,'user_id',actor_user_id,
  'conversation_id',conversation.id,'task_id',v_task_id,
  'input_message_id',v_input_message_id,'turn_id',v_turn_id,
  'workspace_scope',workspace_scope,
  'workspace_owner_id',workspace_owner_id,
  'manifest_source',manifest_source,'assets',assets
 );
END $$;

REVOKE ALL ON FUNCTION get_agent_runtime_resource_manifest_v1(
 UUID,TEXT,UUID,BIGINT,TEXT
) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION get_agent_runtime_resource_manifest_v1(
 UUID,TEXT,UUID,BIGINT,TEXT
) TO everydayai_agent_runtime_worker;

RESET ROLE;
