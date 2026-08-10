-- 227_36: Durable Runtime-owned Web projection receipt plus best-effort wakeup.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_web_projection_receipts(
 intent_id UUID PRIMARY KEY REFERENCES agent_runtime_scheduled_delivery_intents(id) ON DELETE RESTRICT,
 scheduled_run_id UUID NOT NULL REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
 runtime_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 target_hash TEXT NOT NULL CHECK(target_hash~'^[0-9a-f]{64}$'),
 content_identity_hash TEXT NOT NULL CHECK(content_identity_hash~'^[0-9a-f]{64}$'),
 projection_state TEXT NOT NULL DEFAULT 'pending'
  CHECK(projection_state IN('pending','claimed','projected','completed','unavailable')),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 claim_worker_id TEXT CHECK(claim_worker_id IS NULL OR length(claim_worker_id) BETWEEN 1 AND 128),
 claim_request_id UUID UNIQUE,
 claim_token UUID,
 claim_lease_expires_at TIMESTAMPTZ,
 scheduled_run_status TEXT CHECK(scheduled_run_status IN('success','failed','skipped')),
 task_status TEXT CHECK(task_status IN('active','paused','error','running')),
 terminal_status TEXT CHECK(terminal_status IN('completed','failed','cancelled')),
 summary TEXT CHECK(summary IS NULL OR length(summary)<=500),
 reason_code TEXT CHECK(reason_code IS NULL OR reason_code~'^[a-z0-9_]{1,80}$'),
 next_run_at TIMESTAMPTZ,
 consecutive_failures INTEGER CHECK(consecutive_failures IS NULL OR consecutive_failures>=0),
 projection_receipt_hash TEXT UNIQUE CHECK(
  projection_receipt_hash IS NULL OR projection_receipt_hash~'^[0-9a-f]{64}$'),
 projected_at TIMESTAMPTZ,
 wakeup_result TEXT CHECK(wakeup_result IN('sent','failed')),
 wakeup_error_code TEXT CHECK(
  wakeup_error_code IS NULL OR wakeup_error_code~'^[a-z0-9_]{1,80}$'),
 wakeup_attempted_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(intent_id,target_hash,content_identity_hash),
 CHECK((projected_at IS NULL)=(projection_receipt_hash IS NULL)),
 CHECK((wakeup_attempted_at IS NULL)=(wakeup_result IS NULL)),
 CHECK((wakeup_result='failed')=(wakeup_error_code IS NOT NULL)),
 CHECK(projection_state<>'completed' OR wakeup_attempted_at IS NOT NULL),
 CHECK((claim_token IS NULL)=(claim_lease_expires_at IS NULL)),
 CHECK((claim_token IS NULL)=(claim_worker_id IS NULL))
);

CREATE TABLE agent_runtime_scheduled_web_wakeup_attempts(
 intent_id UUID PRIMARY KEY REFERENCES agent_runtime_scheduled_web_projection_receipts(intent_id)
  ON DELETE RESTRICT,
 claim_token UUID NOT NULL UNIQUE,
 result TEXT NOT NULL CHECK(result IN('sent','failed')),
 error_code TEXT CHECK(error_code IS NULL OR error_code~'^[a-z0-9_]{1,80}$'),
 attempted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK((result='failed')=(error_code IS NOT NULL))
);

CREATE INDEX idx_runtime_scheduled_web_projection_claim
 ON agent_runtime_scheduled_web_projection_receipts(projection_state,claim_lease_expires_at,created_at)
 WHERE projection_state IN('pending','claimed','projected');

ALTER TABLE agent_runtime_scheduled_web_projection_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_web_projection_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_web_wakeup_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_web_wakeup_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_web_projection_receipts_owner
 ON agent_runtime_scheduled_web_projection_receipts FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_web_wakeup_attempts_owner
 ON agent_runtime_scheduled_web_wakeup_attempts FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);

REVOKE ALL ON agent_runtime_scheduled_web_projection_receipts,
 agent_runtime_scheduled_web_wakeup_attempts
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_web_projection_facts(p_intent_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE i agent_runtime_scheduled_delivery_intents%ROWTYPE;
 target agent_runtime_scheduled_delivery_targets%ROWTYPE;
 content agent_runtime_scheduled_delivery_contents%ROWTYPE;
 binding agent_runtime_scheduled_delivery_runtime_bindings%ROWTYPE;
 finalization agent_runtime_scheduled_finalization_intents%ROWTYPE;
 run scheduled_task_runs%ROWTYPE;task scheduled_tasks%ROWTYPE;
 expected_run_status TEXT;receipt_task_status TEXT;
BEGIN
 SELECT * INTO i FROM agent_runtime_scheduled_delivery_intents WHERE id=p_intent_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO target FROM agent_runtime_scheduled_delivery_targets
  WHERE scheduled_run_id=i.scheduled_run_id AND target_key=i.target_key;
 IF target.scheduled_run_id IS NULL OR target.target_type<>'web' THEN
  RETURN jsonb_build_object('outcome','unsupported');
 END IF;
 SELECT * INTO content FROM agent_runtime_scheduled_delivery_contents
  WHERE scheduled_run_id=i.scheduled_run_id;
 SELECT * INTO binding FROM agent_runtime_scheduled_delivery_runtime_bindings
  WHERE scheduled_run_id=i.scheduled_run_id;
 SELECT * INTO finalization FROM agent_runtime_scheduled_finalization_intents
  WHERE scheduled_run_id=i.scheduled_run_id;
 SELECT * INTO run FROM scheduled_task_runs WHERE id=i.scheduled_run_id;
 SELECT * INTO task FROM scheduled_tasks WHERE id=i.scheduled_task_id;
 expected_run_status:=CASE i.terminal_status WHEN 'completed' THEN 'success'
  WHEN 'failed' THEN 'failed' ELSE 'skipped' END;
 receipt_task_status:=finalization.application_receipt->>'task_status';
 IF content.scheduled_run_id IS NULL OR binding.scheduled_run_id IS NULL
 OR finalization.scheduled_run_id IS NULL OR run.id IS NULL OR task.id IS NULL
 OR(i.scheduled_run_id,i.runtime_run_id,i.scheduled_task_id,i.org_id,i.user_id,
    i.target_hash,i.content_identity_hash)
   IS DISTINCT FROM(target.scheduled_run_id,binding.runtime_run_id,task.id,task.org_id,
    task.user_id,target.target_hash,content.content_identity_hash)
 OR(content.runtime_run_id,content.terminal_status,content.result_hash,content.reason_code)
   IS DISTINCT FROM(i.runtime_run_id,i.terminal_status,i.result_hash,i.reason_code)
 OR(target.target_snapshot->>'type',target.target_snapshot->>'org_id',
    target.target_snapshot->>'user_id')
   IS DISTINCT FROM('web',i.org_id::TEXT,i.user_id::TEXT)
 OR finalization.status<>'applied'
 OR(finalization.runtime_run_id,finalization.scheduled_task_id,finalization.org_id,
    finalization.user_id,finalization.application_request_id,finalization.application_hash)
   IS DISTINCT FROM(i.runtime_run_id,i.scheduled_task_id,i.org_id,i.user_id,
    i.finalization_request_id,i.finalization_application_hash)
 OR(finalization.application_receipt->>'scheduled_run_id',
    finalization.application_receipt->>'scheduled_task_id',
    finalization.application_receipt->>'terminal_status',
    finalization.application_receipt->>'scheduled_run_status')
   IS DISTINCT FROM(i.scheduled_run_id::TEXT,i.scheduled_task_id::TEXT,
    i.terminal_status,expected_run_status)
 OR receipt_task_status NOT IN('active','paused','error','running')
 OR(run.task_id,run.org_id,run.status)
   IS DISTINCT FROM(i.scheduled_task_id,i.org_id,expected_run_status)
 OR task.status IS DISTINCT FROM receipt_task_status
 OR(i.terminal_status='completed' AND task.last_summary IS DISTINCT FROM run.result_summary)
 OR length(coalesce(run.result_summary,''))>500 THEN
  RETURN jsonb_build_object('outcome','fenced','reason_code','projection_binding_fenced');
 END IF;
 IF NOT EXISTS(SELECT 1 FROM org_members member WHERE member.org_id=i.org_id
  AND member.user_id=i.user_id AND member.status='active') THEN
  RETURN jsonb_build_object('outcome','unavailable','reason_code','delivery_member_unavailable');
 END IF;
 RETURN jsonb_build_object('outcome','found','intent_id',i.id,
  'scheduled_run_id',i.scheduled_run_id,'runtime_run_id',i.runtime_run_id,
  'task_id',i.scheduled_task_id,'org_id',i.org_id,'user_id',i.user_id,
  'target_hash',i.target_hash,'content_identity_hash',i.content_identity_hash,
  'terminal_status',i.terminal_status,'scheduled_run_status',run.status,
  'task_status',task.status,'summary',CASE WHEN i.terminal_status='completed'
   THEN run.result_summary ELSE NULL END,
  'reason_code',CASE WHEN i.terminal_status='completed' THEN NULL ELSE i.reason_code END,
  'next_run_at',task.next_run_at,'consecutive_failures',task.consecutive_failures);
END $$;

CREATE FUNCTION _agent_runtime_scheduled_web_projection_payload(
 p_receipt agent_runtime_scheduled_web_projection_receipts) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('intent_id',p_receipt.intent_id,
  'scheduled_run_id',p_receipt.scheduled_run_id,'runtime_run_id',p_receipt.runtime_run_id,
  'task_id',p_receipt.scheduled_task_id,'org_id',p_receipt.org_id,
  'user_id',p_receipt.user_id,'target_hash',p_receipt.target_hash,
  'content_identity_hash',p_receipt.content_identity_hash,
  'terminal_status',p_receipt.terminal_status,
  'scheduled_run_status',p_receipt.scheduled_run_status,
  'task_status',p_receipt.task_status,'summary',p_receipt.summary,
  'reason_code',p_receipt.reason_code,'next_run_at',p_receipt.next_run_at,
  'consecutive_failures',p_receipt.consecutive_failures,
  'projection_receipt_hash',p_receipt.projection_receipt_hash,
  'projected_at',p_receipt.projected_at,'projection_state',p_receipt.projection_state,
  'state_version',p_receipt.state_version,
  'claim_request_id',p_receipt.claim_request_id,'claim_token',p_receipt.claim_token,
  'claim_lease_expires_at',p_receipt.claim_lease_expires_at,
  'wakeup_result',p_receipt.wakeup_result,
  'wakeup_error_code',p_receipt.wakeup_error_code,
  'wakeup_attempted_at',p_receipt.wakeup_attempted_at)
$$;

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
 INSERT INTO agent_runtime_scheduled_web_projection_receipts(
  intent_id,scheduled_run_id,runtime_run_id,scheduled_task_id,org_id,user_id,
  target_hash,content_identity_hash)
 SELECT i.id,i.scheduled_run_id,i.runtime_run_id,i.scheduled_task_id,i.org_id,i.user_id,
  i.target_hash,i.content_identity_hash FROM agent_runtime_scheduled_delivery_intents i
 JOIN agent_runtime_scheduled_delivery_targets target
  ON(target.scheduled_run_id,target.target_key,target.target_hash)=
    (i.scheduled_run_id,i.target_key,i.target_hash)
 WHERE target.target_type='web' ON CONFLICT(intent_id) DO NOTHING;
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

CREATE FUNCTION apply_agent_runtime_scheduled_web_projection_v1(
 p_intent_id UUID,p_claim_token UUID,p_expected_state_version BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE receipt agent_runtime_scheduled_web_projection_receipts%ROWTYPE;
 facts JSONB;receipt_hash TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO receipt FROM agent_runtime_scheduled_web_projection_receipts
  WHERE intent_id=p_intent_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF receipt.claim_token IS DISTINCT FROM p_claim_token
 OR receipt.claim_lease_expires_at<=clock_timestamp()
 OR receipt.state_version IS DISTINCT FROM p_expected_state_version THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_CLAIM_FENCED' USING ERRCODE='40001';
 END IF;
 facts:=_agent_runtime_scheduled_web_projection_facts(p_intent_id);
 IF facts->>'outcome'<>'found'
 OR(facts->>'target_hash',facts->>'content_identity_hash',facts->>'scheduled_run_id',
    facts->>'runtime_run_id',facts->>'task_id',facts->>'org_id',facts->>'user_id')
   IS DISTINCT FROM(receipt.target_hash,receipt.content_identity_hash,
    receipt.scheduled_run_id::TEXT,receipt.runtime_run_id::TEXT,
    receipt.scheduled_task_id::TEXT,receipt.org_id::TEXT,receipt.user_id::TEXT) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_APPLY_FENCED' USING ERRCODE='40001';
 END IF;
 IF receipt.projected_at IS NOT NULL THEN
  RETURN jsonb_build_object('outcome','already_projected')
   ||_agent_runtime_scheduled_web_projection_payload(receipt);
 END IF;
 receipt_hash:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(
  facts-'outcome'),'UTF8'),'sha256'),'hex');
 UPDATE agent_runtime_scheduled_web_projection_receipts SET
  projection_state='projected',scheduled_run_status=facts->>'scheduled_run_status',
  task_status=facts->>'task_status',terminal_status=facts->>'terminal_status',
  summary=facts->>'summary',reason_code=facts->>'reason_code',
  next_run_at=(facts->>'next_run_at')::TIMESTAMPTZ,
  consecutive_failures=(facts->>'consecutive_failures')::INTEGER,
  projection_receipt_hash=receipt_hash,projected_at=clock_timestamp(),
  updated_at=clock_timestamp()
 WHERE intent_id=p_intent_id RETURNING * INTO receipt;
 RETURN jsonb_build_object('outcome','projected')
  ||_agent_runtime_scheduled_web_projection_payload(receipt);
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_web_projection_claim_v1(p_request_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE receipt agent_runtime_scheduled_web_projection_receipts%ROWTYPE;facts JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO receipt FROM agent_runtime_scheduled_web_projection_receipts
  WHERE claim_request_id=p_request_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 facts:=_agent_runtime_scheduled_web_projection_facts(receipt.intent_id);
 IF facts->>'outcome'<>'found' THEN
  RETURN jsonb_build_object('outcome',facts->>'outcome','intent_id',receipt.intent_id,
   'reason_code',facts->>'reason_code','state_version',receipt.state_version);
 END IF;
 RETURN jsonb_build_object('outcome','claimed')
  ||_agent_runtime_scheduled_web_projection_payload(receipt)||facts-'outcome';
END $$;

CREATE FUNCTION get_agent_runtime_scheduled_web_projection_v1(p_intent_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE receipt agent_runtime_scheduled_web_projection_receipts%ROWTYPE;facts JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO receipt FROM agent_runtime_scheduled_web_projection_receipts
  WHERE intent_id=p_intent_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF receipt.projected_at IS NULL THEN
  RETURN jsonb_build_object('outcome',receipt.projection_state)
   ||_agent_runtime_scheduled_web_projection_payload(receipt);
 END IF;
 facts:=_agent_runtime_scheduled_web_projection_facts(p_intent_id);
 IF facts->>'outcome'<>'found' THEN
  RETURN jsonb_build_object('outcome','fenced','reason_code','projection_readback_fenced');
 END IF;
 RETURN jsonb_build_object('outcome','projected')
  ||_agent_runtime_scheduled_web_projection_payload(receipt);
END $$;

CREATE FUNCTION complete_agent_runtime_scheduled_web_wakeup_v1(
 p_intent_id UUID,p_claim_token UUID,p_expected_state_version BIGINT,
 p_delivered BOOLEAN,p_error_code TEXT DEFAULT NULL) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE receipt agent_runtime_scheduled_web_projection_receipts%ROWTYPE;
 result_code TEXT:=CASE WHEN p_delivered THEN 'sent' ELSE 'failed' END;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 IF (p_delivered AND p_error_code IS NOT NULL) OR(NOT p_delivered AND
  coalesce(p_error_code,'')!~'^[a-z0-9_]{1,80}$') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_WAKEUP_RESULT_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO receipt FROM agent_runtime_scheduled_web_projection_receipts
  WHERE intent_id=p_intent_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF receipt.projection_state='completed' THEN
  IF receipt.state_version IS DISTINCT FROM p_expected_state_version+1
  OR NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_web_wakeup_attempts attempt
   WHERE attempt.intent_id=p_intent_id AND attempt.claim_token=p_claim_token) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_WAKEUP_CLAIM_FENCED' USING ERRCODE='40001';
  END IF;
  RETURN jsonb_build_object('outcome','already_completed')
   ||_agent_runtime_scheduled_web_projection_payload(receipt);
 END IF;
 IF receipt.projected_at IS NULL OR receipt.claim_token IS DISTINCT FROM p_claim_token
 OR receipt.claim_lease_expires_at<=clock_timestamp()
 OR receipt.state_version IS DISTINCT FROM p_expected_state_version THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_WAKEUP_CLAIM_FENCED' USING ERRCODE='40001';
 END IF;
 INSERT INTO agent_runtime_scheduled_web_wakeup_attempts(
  intent_id,claim_token,result,error_code)
 VALUES(p_intent_id,p_claim_token,result_code,p_error_code);
 UPDATE agent_runtime_scheduled_web_projection_receipts SET
  projection_state='completed',wakeup_result=result_code,wakeup_error_code=p_error_code,
  wakeup_attempted_at=clock_timestamp(),claim_worker_id=NULL,claim_token=NULL,
  claim_lease_expires_at=NULL,state_version=state_version+1,updated_at=clock_timestamp()
 WHERE intent_id=p_intent_id RETURNING * INTO receipt;
 RETURN jsonb_build_object('outcome','completed')
  ||_agent_runtime_scheduled_web_projection_payload(receipt);
END $$;

REVOKE ALL ON FUNCTION
 _agent_runtime_scheduled_web_projection_facts(UUID),
 _agent_runtime_scheduled_web_projection_payload(agent_runtime_scheduled_web_projection_receipts),
 claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER),
 apply_agent_runtime_scheduled_web_projection_v1(UUID,UUID,BIGINT),
 read_agent_runtime_scheduled_web_projection_claim_v1(UUID),
 get_agent_runtime_scheduled_web_projection_v1(UUID),
 complete_agent_runtime_scheduled_web_wakeup_v1(UUID,UUID,BIGINT,BOOLEAN,TEXT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
 claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER),
 apply_agent_runtime_scheduled_web_projection_v1(UUID,UUID,BIGINT),
 read_agent_runtime_scheduled_web_projection_claim_v1(UUID),
 get_agent_runtime_scheduled_web_projection_v1(UUID),
 complete_agent_runtime_scheduled_web_wakeup_v1(UUID,UUID,BIGINT,BOOLEAN,TEXT)
 TO everydayai_projection_worker;

RESET ROLE;
