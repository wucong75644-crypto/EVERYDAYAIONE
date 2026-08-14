-- 228.08h: impose one receipt-materialization order for concurrent Web claims.

SET LOCAL ROLE everydayai_owner;

DO $guard$
DECLARE current_definition TEXT;
BEGIN
 IF to_regclass('public.agent_runtime_scheduled_web_projection_receipts') IS NULL
 OR to_regprocedure(
  'claim_agent_runtime_scheduled_web_projection_v1(text,uuid,integer)'
 ) IS NULL
 OR to_regprocedure(
  '_claim_agent_runtime_scheduled_web_projection_227_36_v1(text,uuid,integer)'
 ) IS NOT NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_228_08H_IDENTITY_CONFLICT' USING ERRCODE='55000';
 END IF;
 SELECT pg_get_functiondef(
  'claim_agent_runtime_scheduled_web_projection_v1(text,uuid,integer)'::REGPROCEDURE
 ) INTO current_definition;
 IF current_definition NOT LIKE
  '%WHERE target.target_type=''web'' ON CONFLICT(intent_id) DO NOTHING;%'
 OR obj_description(
  'claim_agent_runtime_scheduled_web_projection_v1(text,uuid,integer)'::REGPROCEDURE,
  'pg_proc'
 ) IS NOT NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_228_08H_PREDECESSOR_MISMATCH' USING ERRCODE='55000';
 END IF;
END
$guard$;

ALTER FUNCTION claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER)
 RENAME TO _claim_agent_runtime_scheduled_web_projection_227_36_v1;
REVOKE ALL ON FUNCTION
 _claim_agent_runtime_scheduled_web_projection_227_36_v1(TEXT,UUID,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker,everydayai_runtime_admin;

CREATE FUNCTION claim_agent_runtime_scheduled_web_projection_v1(
 p_worker_id TEXT,p_request_id UUID,p_lease_seconds INTEGER DEFAULT 60) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE receipt agent_runtime_scheduled_web_projection_receipts%ROWTYPE;
 facts JSONB;token UUID:=gen_random_uuid();
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 IF p_request_id IS NULL OR length(btrim(coalesce(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR p_lease_seconds NOT BETWEEN 5 AND 300 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_CLAIM_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO receipt FROM agent_runtime_scheduled_web_projection_receipts
  WHERE claim_request_id=p_request_id;
 IF FOUND THEN RETURN jsonb_build_object('outcome','claimed')
  ||_agent_runtime_scheduled_web_projection_payload(receipt); END IF;
 /* The transaction lock is taken only while a receipt gap exists. PostgreSQL
    can otherwise deadlock one speculative tuple across the two unique indexes,
    even when every INSERT input is ordered. The second check runs after wait. */
 IF EXISTS(
  SELECT 1 FROM agent_runtime_scheduled_delivery_intents i
  JOIN agent_runtime_scheduled_delivery_targets target
   ON(target.scheduled_run_id,target.target_key,target.target_hash)=
     (i.scheduled_run_id,i.target_key,i.target_hash)
  WHERE target.target_type='web'
  AND NOT EXISTS(
   SELECT 1 FROM agent_runtime_scheduled_web_projection_receipts existing
   WHERE existing.intent_id=i.id
  )
 ) THEN
  PERFORM pg_advisory_xact_lock(228080036::BIGINT);
  INSERT INTO agent_runtime_scheduled_web_projection_receipts(
   intent_id,scheduled_run_id,runtime_run_id,scheduled_task_id,org_id,user_id,
   target_hash,content_identity_hash)
  SELECT i.id,i.scheduled_run_id,i.runtime_run_id,i.scheduled_task_id,i.org_id,i.user_id,
   i.target_hash,i.content_identity_hash FROM agent_runtime_scheduled_delivery_intents i
  JOIN agent_runtime_scheduled_delivery_targets target
   ON(target.scheduled_run_id,target.target_key,target.target_hash)=
     (i.scheduled_run_id,i.target_key,i.target_hash)
  WHERE target.target_type='web'
  AND NOT EXISTS(
   SELECT 1 FROM agent_runtime_scheduled_web_projection_receipts existing
   WHERE existing.intent_id=i.id
  )
  ORDER BY i.id ASC
  ON CONFLICT(intent_id) DO NOTHING;
 END IF;
 SELECT * INTO receipt FROM agent_runtime_scheduled_web_projection_receipts
  WHERE projection_state IN('pending','claimed','projected')
  AND(projection_state='pending' OR claim_lease_expires_at<=clock_timestamp()
      OR claim_lease_expires_at IS NULL)
  ORDER BY created_at,intent_id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 facts:=_agent_runtime_scheduled_web_projection_facts(receipt.intent_id);
 IF facts->>'outcome' IN('unavailable','fenced') THEN
  UPDATE agent_runtime_scheduled_web_projection_receipts SET
   projection_state='unavailable',reason_code=facts->>'reason_code',
   claim_worker_id=NULL,claim_token=NULL,claim_lease_expires_at=NULL,
   state_version=state_version+1,updated_at=clock_timestamp()
  WHERE intent_id=receipt.intent_id RETURNING * INTO receipt;
  RETURN jsonb_build_object('outcome',facts->>'outcome','intent_id',receipt.intent_id,
   'reason_code',receipt.reason_code,'state_version',receipt.state_version);
 ELSIF facts->>'outcome'<>'found' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_FACTS_INVALID' USING ERRCODE='55000';
 END IF;
 UPDATE agent_runtime_scheduled_web_projection_receipts SET
  projection_state=CASE WHEN projected_at IS NULL THEN 'claimed' ELSE 'projected' END,
  claim_worker_id=btrim(p_worker_id),claim_request_id=p_request_id,claim_token=token,
  claim_lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
  state_version=state_version+1,updated_at=clock_timestamp()
 WHERE intent_id=receipt.intent_id RETURNING * INTO receipt;
 RETURN jsonb_build_object('outcome','claimed')
  ||_agent_runtime_scheduled_web_projection_payload(receipt)||facts-'outcome';
END $$;

COMMENT ON FUNCTION claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER)
 IS '228_08h ordered scheduled Web projection receipt materialization';
REVOKE ALL ON FUNCTION
 claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION
 claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER)
 TO everydayai_projection_worker;

RESET ROLE;
