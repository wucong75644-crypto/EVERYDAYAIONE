-- 227_38: WeCom Runtime claim/readback/lease RPCs with per-intent live target validation.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _assert_agent_runtime_scheduled_wecom_actor() RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_wecom_runtime'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'worker' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_live_context(p_intent_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 i agent_runtime_scheduled_delivery_intents%ROWTYPE;
 target agent_runtime_scheduled_delivery_targets%ROWTYPE;
 content agent_runtime_scheduled_delivery_contents%ROWTYPE;
 live_target JSONB;
BEGIN
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO i FROM agent_runtime_scheduled_delivery_intents WHERE id=d.intent_id;
 SELECT * INTO target FROM agent_runtime_scheduled_delivery_targets
  WHERE scheduled_run_id=d.scheduled_run_id AND target_key=d.target_key;
 SELECT * INTO content FROM agent_runtime_scheduled_delivery_contents
  WHERE scheduled_run_id=d.scheduled_run_id AND content_identity_hash=d.content_identity_hash;
 IF i.id IS NULL OR target.scheduled_run_id IS NULL OR content.scheduled_run_id IS NULL
 OR(d.scheduled_run_id,d.runtime_run_id,d.scheduled_task_id,d.org_id,d.user_id,d.target_key,
    d.target_hash,d.content_identity_hash)
   IS DISTINCT FROM(i.scheduled_run_id,i.runtime_run_id,i.scheduled_task_id,i.org_id,i.user_id,
    i.target_key,i.target_hash,i.content_identity_hash)
 OR(d.target_hash,d.target_type,d.target_snapshot)
   IS DISTINCT FROM(target.target_hash,target.target_type,target.target_snapshot)
 OR(content.runtime_run_id,content.terminal_status,content.result_hash,content.reason_code,
    content.content_identity_hash)
   IS DISTINCT FROM(i.runtime_run_id,i.terminal_status,i.result_hash,i.reason_code,
    i.content_identity_hash)
 OR NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_run_bindings binding
  JOIN agent_runs r ON r.id=binding.runtime_run_id
  JOIN scheduled_task_runs q ON q.id=binding.scheduled_run_id
  JOIN scheduled_tasks t ON t.id=binding.scheduled_task_id
  WHERE binding.scheduled_run_id=d.scheduled_run_id
   AND(binding.scheduled_task_id,binding.org_id,binding.user_id,binding.owner_kind,binding.runtime_run_id)
    IS NOT DISTINCT FROM(d.scheduled_task_id,d.org_id,d.user_id,'runtime',d.runtime_run_id)
   AND(r.org_id,r.user_id,r.run_kind,r.status)
    IS NOT DISTINCT FROM(d.org_id,d.user_id,'scheduled',i.terminal_status)
   AND(q.task_id,q.org_id) IS NOT DISTINCT FROM(d.scheduled_task_id,d.org_id)
   AND(t.org_id,t.user_id) IS NOT DISTINCT FROM(d.org_id,d.user_id)) THEN
  RETURN jsonb_build_object('outcome','unavailable','reason_code','wecom_contract_unavailable');
 END IF;
 IF NOT EXISTS(SELECT 1 FROM organizations o WHERE o.id=d.org_id AND o.status='active') THEN
  RETURN jsonb_build_object('outcome','unavailable','reason_code','wecom_org_unavailable');
 END IF;
 IF NOT EXISTS(SELECT 1 FROM org_members m WHERE m.org_id=d.org_id
  AND m.user_id=d.user_id AND m.status='active') THEN
  RETURN jsonb_build_object('outcome','unavailable','reason_code','wecom_member_unavailable');
 END IF;
 IF d.target_type='wecom_user' AND d.target_snapshot->>'channel'='app' THEN
  SELECT jsonb_build_object('type','wecom_user','mapping_id',m.id,'org_id',m.org_id,
   'corp_id',m.corp_id,'user_id',m.user_id,'wecom_userid',m.wecom_userid,'channel',m.channel)
   INTO live_target FROM wecom_user_mappings m JOIN org_members member
    ON(member.org_id,member.user_id,member.status)=(m.org_id,m.user_id,'active')
   WHERE m.id=(d.target_snapshot->>'mapping_id')::UUID AND m.org_id=d.org_id
    AND(m.org_id,m.corp_id,m.user_id,m.wecom_userid,m.channel)
     IS NOT DISTINCT FROM((d.target_snapshot->>'org_id')::UUID,d.target_snapshot->>'corp_id',
      (d.target_snapshot->>'mapping_user_id')::UUID,d.target_snapshot->>'wecom_userid','app');
 ELSIF d.target_type='wecom_user' AND d.target_snapshot->>'channel'='smart_robot' THEN
  SELECT jsonb_build_object('type','wecom_user','mapping_id',m.id,'org_id',m.org_id,
   'corp_id',m.corp_id,'user_id',m.user_id,'wecom_userid',m.wecom_userid,'channel',m.channel,
   'chatid',m.last_chatid,'chat_type',m.last_chat_type) INTO live_target
   FROM wecom_user_mappings m JOIN org_members member
    ON(member.org_id,member.user_id,member.status)=(m.org_id,m.user_id,'active')
   WHERE m.id=(d.target_snapshot->>'mapping_id')::UUID AND m.org_id=d.org_id
    AND(m.org_id,m.corp_id,m.user_id,m.wecom_userid,m.channel)
     IS NOT DISTINCT FROM((d.target_snapshot->>'org_id')::UUID,d.target_snapshot->>'corp_id',
      (d.target_snapshot->>'mapping_user_id')::UUID,d.target_snapshot->>'wecom_userid','smart_robot')
    AND NULLIF(btrim(m.last_chatid),'') IS NOT NULL AND m.last_chat_type IN('single','group');
 ELSIF d.target_type='wecom_group' THEN
  SELECT jsonb_build_object('type','wecom_group','target_id',g.id,'org_id',g.org_id,
   'corp_id',g.corp_id,'chatid',g.chatid,'chat_type',g.chat_type,'channel','smart_robot')
   INTO live_target FROM wecom_chat_targets g
   WHERE g.id=(d.target_snapshot->>'target_id')::UUID AND g.org_id=d.org_id
    AND(g.org_id,g.corp_id,g.chatid,g.chat_type)
     IS NOT DISTINCT FROM((d.target_snapshot->>'org_id')::UUID,d.target_snapshot->>'corp_id',
      d.target_snapshot->>'chatid',d.target_snapshot->>'chat_type') AND g.is_active;
 END IF;
 IF live_target IS NULL THEN
  RETURN jsonb_build_object('outcome','unavailable','reason_code','wecom_target_unavailable');
 END IF;
 RETURN jsonb_build_object('outcome','available','target',live_target,
  'terminal_status',i.terminal_status,'reason_code',i.reason_code,'result_hash',i.result_hash);
EXCEPTION WHEN invalid_text_representation THEN
 RETURN jsonb_build_object('outcome','unavailable','reason_code','wecom_target_unavailable');
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_cancel_unavailable(
 p_intent_id UUID,p_reason_code TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF p_reason_code!~'^[a-z0-9_]{1,80}$'
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts a
  JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id
  WHERE item.intent_id=p_intent_id) THEN RETURN FALSE; END IF;
 UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='cancelled',state_version=state_version+1,
  next_attempt_at=NULL,terminal_reason_code=p_reason_code,updated_at=clock_timestamp()
  WHERE intent_id=p_intent_id AND status IN('pending','retry_wait');
 UPDATE agent_runtime_scheduled_wecom_deliveries SET status='unavailable',state_version=state_version+1,
  claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL,lease_expires_at=NULL,
  next_attempt_at=NULL,terminal_reason_code=p_reason_code,updated_at=clock_timestamp()
  WHERE intent_id=p_intent_id AND status IN('pending','retry_wait','claimed');
 RETURN FOUND;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_claim_json(
 p_delivery agent_runtime_scheduled_wecom_deliveries,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'intent_id',p_delivery.intent_id,
  'claim_request_id',p_delivery.claim_request_id,'worker_id',p_delivery.claim_worker_id,
  'lease_token',p_delivery.lease_token,'lease_expires_at',p_delivery.lease_expires_at,
  'state_version',p_delivery.state_version)
$$;

CREATE FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v1(
 p_claim_request_id UUID,p_worker_id TEXT,p_lease_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;live JSONB;token UUID;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_claim_request_id IS NULL OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR p_lease_seconds NOT BETWEEN 5 AND 900 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CLAIM_INVALID' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-wecom-claim:'||p_claim_request_id,0));
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries
  WHERE claim_request_id=p_claim_request_id FOR UPDATE;
 IF FOUND THEN
  IF d.claim_worker_id IS DISTINCT FROM btrim(p_worker_id) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CLAIM_REQUEST_CONFLICT' USING ERRCODE='55000';
  END IF;
  RETURN _agent_runtime_scheduled_wecom_claim_json(d,'readback');
 END IF;
 LOOP
  SELECT candidate.* INTO d FROM agent_runtime_scheduled_wecom_deliveries candidate
   WHERE((candidate.status IN('pending','retry_wait')
      AND COALESCE(candidate.next_attempt_at,'-infinity'::TIMESTAMPTZ)<=clock_timestamp())
     OR(candidate.status='claimed' AND candidate.lease_expires_at<=clock_timestamp()))
    AND EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items item
     WHERE item.intent_id=candidate.intent_id AND item.status IN('pending','retry_wait')
      AND COALESCE(item.next_attempt_at,'-infinity'::TIMESTAMPTZ)<=clock_timestamp())
    AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts a
     JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id
     WHERE item.intent_id=candidate.intent_id)
   ORDER BY COALESCE(candidate.next_attempt_at,candidate.created_at),candidate.created_at,
    candidate.intent_id FOR UPDATE OF candidate SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN jsonb_build_object('outcome','empty'); END IF;
  live:=_agent_runtime_scheduled_wecom_live_context(d.intent_id);
  IF live->>'outcome'<>'available' THEN
   PERFORM _agent_runtime_scheduled_wecom_cancel_unavailable(
    d.intent_id,COALESCE(live->>'reason_code','wecom_contract_unavailable'));
   CONTINUE;
  END IF;
  token:=gen_random_uuid();
  UPDATE agent_runtime_scheduled_wecom_deliveries SET status='claimed',state_version=state_version+1,
   claim_worker_id=btrim(p_worker_id),claim_request_id=p_claim_request_id,lease_token=token,
   lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),next_attempt_at=NULL,
   terminal_reason_code=NULL,updated_at=clock_timestamp() WHERE intent_id=d.intent_id RETURNING * INTO d;
  RETURN _agent_runtime_scheduled_wecom_claim_json(d,'claimed');
 END LOOP;
END $$;

CREATE FUNCTION renew_agent_runtime_scheduled_wecom_delivery_lease_v1(
 p_intent_id UUID,p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_expected_state_version BIGINT,p_lease_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_intent_id IS NULL OR p_claim_request_id IS NULL OR p_lease_token IS NULL
 OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR p_expected_state_version<1 OR p_lease_seconds NOT BETWEEN 5 AND 900 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RENEW_INVALID' USING ERRCODE='22023';
 END IF;
 UPDATE agent_runtime_scheduled_wecom_deliveries SET state_version=state_version+1,
  lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),updated_at=clock_timestamp()
  WHERE intent_id=p_intent_id AND status='claimed' AND claim_request_id=p_claim_request_id
   AND lease_token=p_lease_token AND claim_worker_id=btrim(p_worker_id)
   AND state_version=p_expected_state_version AND lease_expires_at>clock_timestamp()
  RETURNING * INTO d;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 RETURN _agent_runtime_scheduled_wecom_claim_json(d,'renewed');
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_wecom_claim_v1(p_claim_request_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_claim_request_id IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_READBACK_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries
  WHERE claim_request_id=p_claim_request_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN _agent_runtime_scheduled_wecom_claim_json(d,'readback')||jsonb_build_object(
  'lease_active',d.status='claimed' AND d.lease_expires_at>clock_timestamp());
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_wecom_dispatch_context_v1(
 p_intent_id UUID,p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_expected_state_version BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;live JSONB;items JSONB;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_intent_id IS NULL OR p_claim_request_id IS NULL OR p_lease_token IS NULL
 OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128 OR p_expected_state_version<1 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTEXT_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF d.status<>'claimed' OR d.claim_request_id IS DISTINCT FROM p_claim_request_id
 OR d.lease_token IS DISTINCT FROM p_lease_token OR d.claim_worker_id IS DISTINCT FROM btrim(p_worker_id)
 OR d.state_version IS DISTINCT FROM p_expected_state_version OR d.lease_expires_at<=clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 live:=_agent_runtime_scheduled_wecom_live_context(d.intent_id);
 IF live->>'outcome'<>'available' THEN
  IF _agent_runtime_scheduled_wecom_cancel_unavailable(
   d.intent_id,COALESCE(live->>'reason_code','wecom_contract_unavailable')) THEN
   RETURN jsonb_build_object('outcome','unavailable','intent_id',d.intent_id,
    'reason_code',COALESCE(live->>'reason_code','wecom_contract_unavailable'));
  END IF;
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('item_id',item.id,'item_key',item.item_key,
  'ordinal',item.ordinal,'item_kind',item.item_kind,'source_role',item.source_role,
  'source_id',item.source_id,'source_revision',item.source_revision,
  'source_identity_hash',item.source_identity_hash,
  'content_identity_hash',item.content_identity_hash) ORDER BY item.ordinal),'[]'::JSONB)
  INTO items FROM agent_runtime_scheduled_wecom_delivery_items item WHERE item.intent_id=d.intent_id;
 RETURN jsonb_build_object('outcome','context','intent_id',d.intent_id,
  'scheduled_run_id',d.scheduled_run_id,'runtime_run_id',d.runtime_run_id,
  'scheduled_task_id',d.scheduled_task_id,'org_id',d.org_id,'user_id',d.user_id,
  'target_key',d.target_key,'target_hash',d.target_hash,'target_type',d.target_type,
  'target',live->'target','content_identity_hash',d.content_identity_hash,
  'provider_revision',d.provider_revision,'terminal_status',live->>'terminal_status',
  'result_hash',live->>'result_hash','reason_code',live->>'reason_code','items',items);
END $$;

COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_claim_v1(UUID) IS
 'Pure current-claim readback; never renews a lease, changes a version, or creates a claim.';
COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_context_v1(UUID,UUID,UUID,TEXT,BIGINT) IS
 'Fenced dispatch gate and safe context read; live target failure atomically marks a pre-dispatch delivery unavailable and cancels pending items.';

REVOKE ALL ON FUNCTION _assert_agent_runtime_scheduled_wecom_actor(),
 _agent_runtime_scheduled_wecom_live_context(UUID),
 _agent_runtime_scheduled_wecom_cancel_unavailable(UUID,TEXT),
 _agent_runtime_scheduled_wecom_claim_json(agent_runtime_scheduled_wecom_deliveries,TEXT),
 claim_agent_runtime_scheduled_wecom_delivery_v1(UUID,TEXT,INTEGER),
 renew_agent_runtime_scheduled_wecom_delivery_lease_v1(UUID,UUID,UUID,TEXT,BIGINT,INTEGER),
 read_agent_runtime_scheduled_wecom_claim_v1(UUID),
 read_agent_runtime_scheduled_wecom_dispatch_context_v1(UUID,UUID,UUID,TEXT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v1(UUID,TEXT,INTEGER),
 renew_agent_runtime_scheduled_wecom_delivery_lease_v1(UUID,UUID,UUID,TEXT,BIGINT,INTEGER),
 read_agent_runtime_scheduled_wecom_claim_v1(UUID),
 read_agent_runtime_scheduled_wecom_dispatch_context_v1(UUID,UUID,UUID,TEXT,BIGINT)
 TO everydayai_wecom_runtime;

RESET ROLE;
