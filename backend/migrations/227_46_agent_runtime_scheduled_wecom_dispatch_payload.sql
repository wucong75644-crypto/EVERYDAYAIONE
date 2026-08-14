-- 227_46: Safe, authoritative Scheduled Runtime WeCom dispatch payload readback.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION read_agent_runtime_scheduled_wecom_dispatch_payload_v1(
 p_intent_id UUID,p_item_id UUID,p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_expected_delivery_state_version BIGINT,p_expected_item_state_version BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE context JSONB;item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 run scheduled_task_runs%ROWTYPE;model_result agent_model_results%ROWTYPE;
 target JSONB;transport_target JSONB;channel TEXT;summary TEXT;derived_summary TEXT;
 payload_hash TEXT;terminal_status TEXT;reason_code TEXT;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_intent_id IS NULL OR p_item_id IS NULL OR p_claim_request_id IS NULL
 OR p_lease_token IS NULL OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR p_expected_delivery_state_version IS NULL OR p_expected_item_state_version IS NULL
 OR p_expected_delivery_state_version<1 OR p_expected_item_state_version<0 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PAYLOAD_INVALID' USING ERRCODE='22023';
 END IF;

 context:=read_agent_runtime_scheduled_wecom_dispatch_context_v1(
  p_intent_id,p_claim_request_id,p_lease_token,p_worker_id,
  p_expected_delivery_state_version);
 IF context->>'outcome'='not_found' THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF context->>'outcome'='fenced' THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF context->>'outcome'='unavailable' THEN
  reason_code:=context->>'reason_code';
  IF reason_code IS NULL OR reason_code!~'^[a-z0-9_]{1,80}$' THEN
   reason_code:='wecom_contract_unavailable';
  END IF;
  RETURN jsonb_build_object('outcome','unavailable','reason_code',reason_code);
 END IF;
 IF context->>'outcome'<>'context' THEN
  RETURN jsonb_build_object('outcome','unavailable',
   'reason_code','wecom_contract_unavailable');
 END IF;

 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=p_item_id FOR SHARE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF item.intent_id IS DISTINCT FROM p_intent_id
 OR item.state_version IS DISTINCT FROM p_expected_item_state_version
 OR item.status NOT IN('pending','retry_wait')
 OR COALESCE(item.next_attempt_at,'-infinity'::TIMESTAMPTZ)>clock_timestamp()
 OR item.content_identity_hash IS DISTINCT FROM context->>'content_identity_hash'
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items earlier
  WHERE earlier.intent_id=item.intent_id AND earlier.ordinal<item.ordinal
   AND earlier.status NOT IN('accepted','failed','cancelled')) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;

 IF item.item_kind='artifact_identity' THEN
  RETURN jsonb_build_object('outcome','unsupported',
   'reason_code','wecom_artifact_identity_unsupported');
 END IF;
 terminal_status:=context->>'terminal_status';
 IF terminal_status='failed' THEN
  RETURN jsonb_build_object('outcome','unsupported',
   'reason_code','wecom_failed_content_unsupported');
 ELSIF terminal_status='cancelled' THEN
  RETURN jsonb_build_object('outcome','unsupported',
   'reason_code','wecom_cancelled_content_unsupported');
 ELSIF terminal_status<>'completed' THEN
  RETURN jsonb_build_object('outcome','unsupported',
   'reason_code','wecom_non_completed_content_unsupported');
 END IF;

 IF item.item_kind<>'text' OR item.source_role<>'text' OR item.source_revision<>1
 OR item.source_identity_hash IS DISTINCT FROM context->>'result_hash'
 OR context->>'result_hash'!~'^[0-9a-f]{64}$' THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 SELECT q.* INTO run FROM scheduled_task_runs q
 JOIN agent_runtime_scheduled_finalization_intents finalization
  ON finalization.scheduled_run_id=q.id AND finalization.status='applied'
 JOIN agent_runtime_scheduled_delivery_contents content
  ON content.scheduled_run_id=q.id
 JOIN agent_model_results model_result_fact
  ON model_result_fact.id=content.model_result_id
  AND model_result_fact.run_id=content.runtime_run_id
  AND model_result_fact.content_hash=content.result_hash
 JOIN agent_runtime_scheduled_delivery_intents intent
  ON intent.scheduled_run_id=q.id AND intent.id=p_intent_id
 WHERE q.id=(context->>'scheduled_run_id')::UUID
  AND(q.task_id,q.org_id,q.status)
   IS NOT DISTINCT FROM((context->>'scheduled_task_id')::UUID,
    (context->>'org_id')::UUID,'success')
  AND(intent.runtime_run_id,intent.scheduled_task_id,intent.org_id,intent.user_id,
      intent.terminal_status,intent.result_hash,intent.content_identity_hash,
      intent.finalization_request_id,intent.finalization_application_hash)
   IS NOT DISTINCT FROM((context->>'runtime_run_id')::UUID,
    (context->>'scheduled_task_id')::UUID,(context->>'org_id')::UUID,
    (context->>'user_id')::UUID,'completed',context->>'result_hash',
    context->>'content_identity_hash',finalization.application_request_id,
    finalization.application_hash)
  AND(content.runtime_run_id,content.terminal_status,content.result_hash,
      content.content_identity_hash)
   IS NOT DISTINCT FROM((context->>'runtime_run_id')::UUID,'completed',
    context->>'result_hash',context->>'content_identity_hash')
  AND(model_result_fact.id,model_result_fact.run_id,model_result_fact.org_id,
      model_result_fact.user_id,model_result_fact.content_hash)
   IS NOT DISTINCT FROM(item.source_id,(context->>'runtime_run_id')::UUID,
    (context->>'org_id')::UUID,(context->>'user_id')::UUID,context->>'result_hash');
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 SELECT model_result_fact.* INTO model_result
 FROM agent_runtime_scheduled_delivery_contents content
 JOIN agent_model_results model_result_fact
  ON model_result_fact.id=content.model_result_id
 WHERE content.scheduled_run_id=run.id
  AND(model_result_fact.id,model_result_fact.run_id,model_result_fact.org_id,
      model_result_fact.user_id,model_result_fact.content_hash)
   IS NOT DISTINCT FROM(item.source_id,(context->>'runtime_run_id')::UUID,
    (context->>'org_id')::UUID,(context->>'user_id')::UUID,context->>'result_hash');
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 derived_summary:=_agent_runtime_scheduled_safe_summary(CASE
  WHEN model_result.output_kind='text' THEN model_result.text_content
  WHEN model_result.output_kind='structured'
   AND jsonb_typeof(model_result.structured_content->'summary')='string'
   THEN model_result.structured_content->>'summary'
  ELSE NULL END);
 summary:=run.result_summary;
 IF summary IS NULL OR length(summary) NOT BETWEEN 1 AND 500
 OR summary IS DISTINCT FROM derived_summary THEN
  RETURN jsonb_build_object('outcome','unavailable',
   'reason_code','wecom_safe_text_unavailable');
 END IF;

 target:=context->'target';channel:=target->>'channel';
 IF target->>'org_id' IS DISTINCT FROM context->>'org_id' THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 IF channel='app' AND target->>'type'='wecom_user'
 AND NULLIF(btrim(target->>'corp_id'),'') IS NOT NULL
 AND NULLIF(btrim(target->>'wecom_userid'),'') IS NOT NULL THEN
  transport_target:=jsonb_build_object(
   'org_id',(target->>'org_id')::UUID,'corp_id',target->>'corp_id',
   'wecom_userid',target->>'wecom_userid');
 ELSIF channel='smart_robot' AND target->>'type' IN('wecom_user','wecom_group')
 AND NULLIF(btrim(target->>'chatid'),'') IS NOT NULL THEN
  transport_target:=jsonb_build_object(
   'org_id',(target->>'org_id')::UUID,'chatid',target->>'chatid');
 ELSE
  RETURN jsonb_build_object('outcome','fenced');
 END IF;

 payload_hash:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(
  jsonb_build_object('payload_revision',1,'scheduled_run_id',run.id,
   'intent_id',p_intent_id,'item_id',item.id,'item_key',item.item_key,
   'ordinal',item.ordinal,'source_revision',item.source_revision,
   'source_identity_hash',item.source_identity_hash,
   'content_identity_hash',item.content_identity_hash,
   'target_hash',context->>'target_hash','org_id',(target->>'org_id')::UUID,
   'channel',channel,'target',transport_target,
   'provider_revision',(context->>'provider_revision')::BIGINT,
   'message_type','text','text',summary)),'UTF8'),'sha256'),'hex');
 RETURN jsonb_build_object('outcome','payload','payload_revision',1,
  'scheduled_run_id',run.id,'intent_id',p_intent_id,'item_id',item.id,
  'item_key',item.item_key,'ordinal',item.ordinal,'item_kind',item.item_kind,
  'source_role',item.source_role,'source_revision',item.source_revision,
  'source_identity_hash',item.source_identity_hash,
  'content_identity_hash',item.content_identity_hash,'result_hash',context->>'result_hash',
  'target_hash',context->>'target_hash','channel',channel,'target',transport_target,
  'provider_revision',(context->>'provider_revision')::BIGINT,
  'delivery_state_version',p_expected_delivery_state_version,
  'item_state_version',item.state_version,'message_type','text','text',summary,
  'payload_hash',payload_hash);
EXCEPTION WHEN invalid_text_representation THEN
 RETURN jsonb_build_object('outcome','fenced');
END $$;

COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_payload_v1(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT) IS
 'Fenced safe text payload readback; inherits the approved 227_38 pre-attempt unavailable cancellation and never exposes raw model or artifact content.';

REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_payload_v1(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_payload_v1(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT) TO everydayai_wecom_runtime;

RESET ROLE;
