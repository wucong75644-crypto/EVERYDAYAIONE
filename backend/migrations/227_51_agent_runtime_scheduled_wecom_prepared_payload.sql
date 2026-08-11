-- 227_51: Pure, identity-bound payload readback for prepared recovery.

SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE candidate REGPROCEDURE;
BEGIN
 candidate:=to_regprocedure(
  'public.read_agent_runtime_scheduled_wecom_dispatch_payload_v1(uuid,uuid,uuid,uuid,text,bigint,bigint)');
 IF candidate IS NULL
 OR to_regprocedure('public._agent_runtime_scheduled_wecom_live_context(uuid)') IS NULL
 OR to_regprocedure(
  'public._agent_runtime_scheduled_wecom_recovery_json(agent_runtime_scheduled_wecom_prepared_recovery_requests,agent_runtime_scheduled_wecom_dispatch_attempts,text)') IS NULL
 OR to_regprocedure(
  'public._agent_runtime_scheduled_wecom_payload_hash_v2(uuid,uuid,uuid,text,integer,bigint,text,text,text,text,uuid,text,jsonb,bigint,bigint,bigint,text,text)') IS NULL
 OR to_regclass('public.agent_runtime_scheduled_wecom_prepared_recovery_requests') IS NULL
 OR to_regprocedure(
  'public.read_agent_runtime_scheduled_wecom_prepared_payload_v1(uuid,uuid,uuid,uuid,integer,uuid,uuid,text,bigint,bigint,text,text,bigint)') IS NOT NULL
 OR EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=candidate
  AND(NOT p.prosecdef OR p.provolatile<>'v'
   OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, public']
   OR pg_get_userbyid(p.proowner)<>'everydayai_owner'))
 OR NOT has_function_privilege('everydayai_wecom_runtime',candidate,'EXECUTE')
 OR has_function_privilege('everydayai_runtime',candidate,'EXECUTE')
 OR EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL
  aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl
  WHERE p.oid=candidate AND acl.grantee=0 AND acl.privilege_type='EXECUTE') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARED_PAYLOAD_DEPENDENCY_DRIFT'
   USING ERRCODE='55000';
 END IF;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_recovery_json(
 p_request agent_runtime_scheduled_wecom_prepared_recovery_requests,
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT _agent_runtime_scheduled_wecom_attempt_json(p_attempt,p_outcome)||jsonb_build_object(
  'intent_id',p_request.intent_id,'claim_request_id',p_request.request_id,
  'worker_id',p_request.worker_id,'lease_token',p_request.lease_token,
  'lease_expires_at',p_request.lease_expires_at,
  'delivery_state_version',p_request.delivery_state_version,
  'item_state_version',p_request.item_state_version,
  'prepared_delivery_state_version',p_attempt.prepared_delivery_state_version,
  'prepared_item_state_version',p_attempt.prepared_item_state_version)
$$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_safe_payload_v2(
 p_context JSONB,p_item agent_runtime_scheduled_wecom_delivery_items,
 p_payload_delivery_state_version BIGINT,p_payload_item_state_version BIGINT) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE run scheduled_task_runs%ROWTYPE;model_result agent_model_results%ROWTYPE;
 target JSONB;transport_target JSONB;channel TEXT;summary TEXT;derived_summary TEXT;
 payload_hash TEXT;terminal_status TEXT;
BEGIN
 IF p_context->>'outcome'<>'context'
 OR p_item.content_identity_hash IS DISTINCT FROM p_context->>'content_identity_hash' THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 IF p_item.item_kind='artifact_identity' THEN
  RETURN jsonb_build_object('outcome','unsupported',
   'reason_code','wecom_artifact_identity_unsupported');
 END IF;
 terminal_status:=p_context->>'terminal_status';
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
 IF p_item.item_kind<>'text' OR p_item.source_role<>'text' OR p_item.source_revision<>1
 OR p_item.source_identity_hash IS DISTINCT FROM p_context->>'result_hash'
 OR p_context->>'result_hash'!~'^[0-9a-f]{64}$' THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 SELECT q.* INTO run FROM scheduled_task_runs q
 JOIN agent_runtime_scheduled_finalization_intents finalization
  ON finalization.scheduled_run_id=q.id AND finalization.status='applied'
 JOIN agent_runtime_scheduled_delivery_contents content ON content.scheduled_run_id=q.id
 JOIN agent_model_results model_result_fact
  ON model_result_fact.id=content.model_result_id
  AND model_result_fact.run_id=content.runtime_run_id
  AND model_result_fact.content_hash=content.result_hash
 JOIN agent_runtime_scheduled_delivery_intents intent
  ON intent.scheduled_run_id=q.id AND intent.id=(p_context->>'intent_id')::UUID
 WHERE q.id=(p_context->>'scheduled_run_id')::UUID
  AND(q.task_id,q.org_id,q.status) IS NOT DISTINCT FROM(
   (p_context->>'scheduled_task_id')::UUID,(p_context->>'org_id')::UUID,'success')
  AND(intent.runtime_run_id,intent.scheduled_task_id,intent.org_id,intent.user_id,
      intent.terminal_status,intent.result_hash,intent.content_identity_hash,
      intent.finalization_request_id,intent.finalization_application_hash)
   IS NOT DISTINCT FROM((p_context->>'runtime_run_id')::UUID,
    (p_context->>'scheduled_task_id')::UUID,(p_context->>'org_id')::UUID,
    (p_context->>'user_id')::UUID,'completed',p_context->>'result_hash',
    p_context->>'content_identity_hash',finalization.application_request_id,
    finalization.application_hash)
  AND(content.runtime_run_id,content.terminal_status,content.result_hash,
      content.content_identity_hash)
   IS NOT DISTINCT FROM((p_context->>'runtime_run_id')::UUID,'completed',
    p_context->>'result_hash',p_context->>'content_identity_hash')
  AND(model_result_fact.id,model_result_fact.run_id,model_result_fact.org_id,
      model_result_fact.user_id,model_result_fact.content_hash)
   IS NOT DISTINCT FROM(p_item.source_id,(p_context->>'runtime_run_id')::UUID,
    (p_context->>'org_id')::UUID,(p_context->>'user_id')::UUID,p_context->>'result_hash');
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 SELECT model_result_fact.* INTO model_result
 FROM agent_runtime_scheduled_delivery_contents content
 JOIN agent_model_results model_result_fact ON model_result_fact.id=content.model_result_id
 WHERE content.scheduled_run_id=run.id
  AND(model_result_fact.id,model_result_fact.run_id,model_result_fact.org_id,
      model_result_fact.user_id,model_result_fact.content_hash)
   IS NOT DISTINCT FROM(p_item.source_id,(p_context->>'runtime_run_id')::UUID,
    (p_context->>'org_id')::UUID,(p_context->>'user_id')::UUID,p_context->>'result_hash');
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 derived_summary:=_agent_runtime_scheduled_safe_summary(CASE
  WHEN model_result.output_kind='text' THEN model_result.text_content
  WHEN model_result.output_kind='structured'
   AND jsonb_typeof(model_result.structured_content->'summary')='string'
   THEN model_result.structured_content->>'summary' ELSE NULL END);
 summary:=run.result_summary;
 IF summary IS NULL OR length(summary) NOT BETWEEN 1 AND 500
 OR summary IS DISTINCT FROM derived_summary THEN
  RETURN jsonb_build_object('outcome','unavailable','reason_code','wecom_safe_text_unavailable');
 END IF;
 target:=p_context->'target';channel:=target->>'channel';
 IF target->>'org_id' IS DISTINCT FROM p_context->>'org_id' THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 IF channel='app' AND target->>'type'='wecom_user'
 AND NULLIF(btrim(target->>'corp_id'),'') IS NOT NULL
 AND NULLIF(btrim(target->>'wecom_userid'),'') IS NOT NULL THEN
  transport_target:=jsonb_build_object('org_id',(target->>'org_id')::UUID,
   'corp_id',target->>'corp_id','wecom_userid',target->>'wecom_userid');
 ELSIF channel='smart_robot' AND target->>'type' IN('wecom_user','wecom_group')
 AND NULLIF(btrim(target->>'chatid'),'') IS NOT NULL THEN
  transport_target:=jsonb_build_object(
   'org_id',(target->>'org_id')::UUID,'chatid',target->>'chatid');
 ELSE RETURN jsonb_build_object('outcome','fenced'); END IF;
 payload_hash:=_agent_runtime_scheduled_wecom_payload_hash_v2(
  run.id,(p_context->>'intent_id')::UUID,p_item.id,p_item.item_key,p_item.ordinal,
  p_item.source_revision,p_item.source_identity_hash,p_item.content_identity_hash,
  p_context->>'result_hash',p_context->>'target_hash',(target->>'org_id')::UUID,
  channel,transport_target,(p_context->>'provider_revision')::BIGINT,
  p_payload_delivery_state_version,p_payload_item_state_version,'text',summary);
 RETURN jsonb_build_object('outcome','payload','payload_revision',2,
  'scheduled_run_id',run.id,'intent_id',(p_context->>'intent_id')::UUID,
  'item_id',p_item.id,'item_key',p_item.item_key,'ordinal',p_item.ordinal,
  'item_kind',p_item.item_kind,'source_role',p_item.source_role,
  'source_revision',p_item.source_revision,'source_identity_hash',p_item.source_identity_hash,
  'content_identity_hash',p_item.content_identity_hash,'result_hash',p_context->>'result_hash',
  'target_hash',p_context->>'target_hash','channel',channel,'target',transport_target,
  'provider_revision',(p_context->>'provider_revision')::BIGINT,
  'delivery_state_version',p_payload_delivery_state_version,
  'item_state_version',p_payload_item_state_version,'message_type','text','text',summary,
  'payload_hash',payload_hash);
EXCEPTION WHEN invalid_text_representation THEN
 RETURN jsonb_build_object('outcome','fenced');
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_wecom_prepared_payload_v1(
 p_recovery_request_id UUID,p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,
 p_attempt_number INTEGER,p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_expected_delivery_state_version BIGINT,p_expected_item_state_version BIGINT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS JSONB
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE request agent_runtime_scheduled_wecom_prepared_recovery_requests%ROWTYPE;
 d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 attempt agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;live JSONB;context JSONB;reason TEXT;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_recovery_request_id IS NULL OR p_intent_id IS NULL OR p_item_id IS NULL
 OR p_attempt_id IS NULL OR p_attempt_number IS NULL OR p_claim_request_id IS NULL
 OR p_lease_token IS NULL OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR p_expected_delivery_state_version IS NULL OR p_expected_item_state_version IS NULL
 OR p_provider_request_id IS NULL OR p_idempotency_key IS NULL OR p_provider_revision IS NULL
 OR p_attempt_number<1 OR p_expected_delivery_state_version<1
 OR p_expected_item_state_version<1 OR length(btrim(p_provider_request_id)) NOT BETWEEN 8 AND 200
 OR p_idempotency_key!~'^[0-9a-f]{64}$' OR p_provider_revision<1 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARED_PAYLOAD_INVALID'
   USING ERRCODE='22023';
 END IF;
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
  WHERE request_id=p_recovery_request_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=p_item_id;
 SELECT * INTO attempt FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=p_attempt_id;
 IF d.intent_id IS NULL OR item.id IS NULL OR attempt.id IS NULL
 OR(request.request_id,request.intent_id,request.item_id,request.attempt_id,
    request.worker_id,request.lease_token,request.delivery_state_version,request.item_state_version)
  IS DISTINCT FROM(p_recovery_request_id,p_intent_id,p_item_id,p_attempt_id,btrim(p_worker_id),
   p_lease_token,p_expected_delivery_state_version,p_expected_item_state_version)
 OR p_claim_request_id IS DISTINCT FROM p_recovery_request_id
 OR(d.intent_id,d.claim_request_id,d.lease_token,d.claim_worker_id,d.state_version,d.status)
  IS DISTINCT FROM(p_intent_id,p_claim_request_id,p_lease_token,btrim(p_worker_id),
   p_expected_delivery_state_version,'claimed')
 OR d.lease_expires_at IS DISTINCT FROM request.lease_expires_at
 OR d.lease_expires_at<=clock_timestamp()
 OR(item.id,item.intent_id,item.state_version,item.status)
  IS DISTINCT FROM(p_item_id,p_intent_id,p_expected_item_state_version,'dispatching')
 OR(attempt.id,attempt.item_id,attempt.attempt_number,attempt.provider_request_id,
    attempt.idempotency_key,attempt.provider_revision,attempt.status,attempt.dispatch_phase)
  IS DISTINCT FROM(p_attempt_id,p_item_id,p_attempt_number,btrim(p_provider_request_id),
   p_idempotency_key,p_provider_revision,'prepared','prepared')
 OR attempt.dispatch_started_at IS NOT NULL OR attempt.unknown_at IS NOT NULL
 OR attempt.resolved_at IS NOT NULL OR attempt.receipt_type IS NOT NULL
 OR attempt.receipt_hash IS NOT NULL OR attempt.receipt_code IS NOT NULL
 OR attempt.was_ambiguous
 OR d.provider_revision IS DISTINCT FROM p_provider_revision THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 live:=_agent_runtime_scheduled_wecom_live_context(p_intent_id);
 IF live->>'outcome'='not_found' THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF live->>'outcome'<>'available' THEN
  reason:=live->>'reason_code';
  IF reason IS NULL OR reason!~'^[a-z0-9_]{1,80}$' THEN reason:='wecom_contract_unavailable'; END IF;
  RETURN jsonb_build_object('outcome','unavailable','reason_code',reason);
 END IF;
 context:=jsonb_build_object('outcome','context','intent_id',d.intent_id,
  'scheduled_run_id',d.scheduled_run_id,'runtime_run_id',d.runtime_run_id,
  'scheduled_task_id',d.scheduled_task_id,'org_id',d.org_id,'user_id',d.user_id,
  'target_hash',d.target_hash,'target',live->'target',
  'content_identity_hash',d.content_identity_hash,'provider_revision',d.provider_revision,
  'terminal_status',live->>'terminal_status','result_hash',live->>'result_hash');
 RETURN _agent_runtime_scheduled_wecom_safe_payload_v2(
  context,item,attempt.prepared_delivery_state_version,attempt.prepared_item_state_version);
END $$;

COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_prepared_payload_v1(
 UUID,UUID,UUID,UUID,INTEGER,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT) IS
 'Pure revision-2 payload readback bound to one active prepared recovery fence and frozen provider identity.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_recovery_json(
 agent_runtime_scheduled_wecom_prepared_recovery_requests,
 agent_runtime_scheduled_wecom_dispatch_attempts,TEXT),
 _agent_runtime_scheduled_wecom_safe_payload_v2(
 JSONB,agent_runtime_scheduled_wecom_delivery_items,BIGINT,BIGINT),
 read_agent_runtime_scheduled_wecom_prepared_payload_v1(
 UUID,UUID,UUID,UUID,INTEGER,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_wecom_prepared_payload_v1(
 UUID,UUID,UUID,UUID,INTEGER,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT)
 TO everydayai_wecom_runtime;

RESET ROLE;
