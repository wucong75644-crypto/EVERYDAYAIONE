-- 227_42: Durable continuation claims after terminal per-item WeCom attempts.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_wecom_continuation_claim_requests(
 request_id UUID PRIMARY KEY,
 intent_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 1 AND 128),
 claim_kind TEXT NOT NULL CHECK(claim_kind IN('initial','continuation')),
 lease_seconds INTEGER NOT NULL CHECK(lease_seconds BETWEEN 5 AND 900),
 lease_token UUID NOT NULL UNIQUE,lease_expires_at TIMESTAMPTZ NOT NULL,
 previous_claim_request_id UUID,
 delivery_state_version BIGINT NOT NULL CHECK(delivery_state_version>=1),
 item_state_version BIGINT NOT NULL CHECK(item_state_version>=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(request_id,intent_id,item_id)
);
ALTER TABLE agent_runtime_scheduled_wecom_continuation_claim_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_continuation_claim_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_continuation_claim_owner
 ON agent_runtime_scheduled_wecom_continuation_claim_requests
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_continuation_claim_requests
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_continuation_claim_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_CLAIM_IMMUTABLE'
  USING ERRCODE='55000';
END $$;
CREATE TRIGGER runtime_scheduled_wecom_continuation_claim_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_continuation_claim_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_continuation_claim_immutable();

CREATE FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items item
   WHERE(item.id,item.intent_id)=(NEW.item_id,NEW.intent_id)) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_IDENTITY_INVALID'
   USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries d
   WHERE d.claim_request_id=NEW.request_id
    AND(d.intent_id,d.claim_worker_id,d.lease_token,d.lease_expires_at,d.state_version)
     IS DISTINCT FROM(NEW.intent_id,NEW.worker_id,NEW.lease_token,NEW.lease_expires_at,
      NEW.delivery_state_version))
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
   WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_wecom_continuation_global_request_guard BEFORE INSERT
 ON agent_runtime_scheduled_wecom_continuation_claim_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard();

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
   WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_legacy_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE guard_request_id UUID;
BEGIN
 IF TG_TABLE_NAME='agent_runtime_scheduled_wecom_deliveries' THEN
  IF NEW.claim_request_id IS NULL
  OR NEW.claim_request_id IS NOT DISTINCT FROM OLD.claim_request_id THEN RETURN NEW; END IF;
  guard_request_id:=NEW.claim_request_id;
 ELSE guard_request_id:=NEW.request_id;
 END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(guard_request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
   WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests
   WHERE request_id=guard_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_terminalize_unavailable_continuation(
 p_intent_id UUID,p_reason_code TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE accepted_count INTEGER;item_count INTEGER;terminal_status TEXT;
BEGIN
 IF p_reason_code!~'^[a-z0-9_]{1,80}$'
 OR NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts a
  JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id
  WHERE item.intent_id=p_intent_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts a
  JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id
  WHERE item.intent_id=p_intent_id
   AND(a.status NOT IN('accepted','rejected') OR a.dispatch_phase<>'receipt_recorded')) THEN
  RETURN FALSE;
 END IF;
 UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='cancelled',
  state_version=state_version+1,next_attempt_at=NULL,terminal_reason_code=p_reason_code,
  updated_at=clock_timestamp()
  WHERE intent_id=p_intent_id AND status IN('pending','retry_wait');
 IF NOT FOUND THEN RETURN FALSE; END IF;
 SELECT count(*) FILTER(WHERE status='accepted'),count(*)
  INTO accepted_count,item_count FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE intent_id=p_intent_id;
 terminal_status:=CASE WHEN accepted_count=item_count THEN 'completed'
  WHEN accepted_count>0 THEN 'partial' ELSE 'failed' END;
 UPDATE agent_runtime_scheduled_wecom_deliveries SET status=terminal_status,
  state_version=state_version+1,claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL,
  lease_expires_at=NULL,reconcile_worker_id=NULL,reconcile_request_id=NULL,reconcile_token=NULL,
  reconcile_lease_expires_at=NULL,next_attempt_at=NULL,
  terminal_reason_code=CASE terminal_status WHEN 'completed' THEN NULL
   WHEN 'partial' THEN 'wecom_dispatch_partial' ELSE 'wecom_dispatch_failed' END,
  updated_at=clock_timestamp() WHERE intent_id=p_intent_id;
 RETURN FOUND;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_continuation_json(
 p_request agent_runtime_scheduled_wecom_continuation_claim_requests,
 p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'request_id',p_request.request_id,
  'claim_request_id',p_request.request_id,'intent_id',p_request.intent_id,
  'item_id',p_request.item_id,'worker_id',p_request.worker_id,
  'claim_kind',p_request.claim_kind,
  'lease_token',p_request.lease_token,'lease_seconds',p_request.lease_seconds,
  'lease_expires_at',p_request.lease_expires_at,
  'previous_claim_request_id',p_request.previous_claim_request_id,
  'state_version',p_request.delivery_state_version,
  'delivery_state_version',p_request.delivery_state_version,
  'item_state_version',p_request.item_state_version)
$$;

CREATE FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v2(
 p_claim_request_id UUID,p_worker_id TEXT,p_lease_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 request agent_runtime_scheduled_wecom_continuation_claim_requests%ROWTYPE;
 token UUID;previous_request_id UUID;candidate_intent_id UUID;candidate_item_id UUID;
 has_attempts BOOLEAN;
 live JSONB;excluded UUID[]:=ARRAY[]::UUID[];
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_claim_request_id IS NULL OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR p_lease_seconds NOT BETWEEN 5 AND 900 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_CLAIM_INVALID'
   USING ERRCODE='22023';
 END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(p_claim_request_id);
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_continuation_claim_requests
  WHERE request_id=p_claim_request_id;
 IF FOUND THEN
  IF(request.worker_id,request.lease_seconds)
   IS DISTINCT FROM(btrim(p_worker_id),p_lease_seconds) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT'
    USING ERRCODE='55000';
  END IF;
  SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=request.intent_id;
  RETURN _agent_runtime_scheduled_wecom_continuation_json(request,
   CASE WHEN(d.claim_request_id,d.claim_worker_id,d.lease_token)
    IS NOT DISTINCT FROM(request.request_id,request.worker_id,request.lease_token)
    AND d.status='claimed' AND d.lease_expires_at>clock_timestamp()
    THEN 'readback' ELSE 'fenced' END);
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=p_claim_request_id OR reconcile_request_id=p_claim_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
   WHERE claim_request_id=p_claim_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
   WHERE request_id=p_claim_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
   WHERE request_id=p_claim_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
   WHERE request_id=p_claim_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 LOOP
  SELECT candidate_delivery.intent_id,candidate_item.id
   INTO candidate_intent_id,candidate_item_id
   FROM agent_runtime_scheduled_wecom_deliveries candidate_delivery
   JOIN agent_runtime_scheduled_wecom_delivery_items candidate_item
    ON candidate_item.intent_id=candidate_delivery.intent_id
   WHERE NOT(candidate_delivery.intent_id=ANY(excluded))
    AND((candidate_delivery.status IN('pending','retry_wait')
      AND COALESCE(candidate_delivery.next_attempt_at,'-infinity'::TIMESTAMPTZ)<=clock_timestamp()
      AND candidate_delivery.claim_request_id IS NULL AND candidate_delivery.lease_token IS NULL)
     OR(candidate_delivery.status='claimed'
      AND candidate_delivery.lease_expires_at<=clock_timestamp()))
    AND candidate_delivery.reconcile_token IS NULL
    AND candidate_item.status IN('pending','retry_wait')
    AND COALESCE(candidate_item.next_attempt_at,'-infinity'::TIMESTAMPTZ)<=clock_timestamp()
    AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts current_attempt
     WHERE current_attempt.item_id=candidate_item.id)
    AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts unsafe_attempt
     JOIN agent_runtime_scheduled_wecom_delivery_items unsafe_item ON unsafe_item.id=unsafe_attempt.item_id
     WHERE unsafe_item.intent_id=candidate_delivery.intent_id
      AND(unsafe_attempt.status NOT IN('accepted','rejected')
       OR unsafe_attempt.dispatch_phase<>'receipt_recorded'))
    AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items earlier
     WHERE earlier.intent_id=candidate_delivery.intent_id AND earlier.ordinal<candidate_item.ordinal
      AND earlier.status NOT IN('accepted','failed','cancelled'))
    AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items later
     WHERE later.intent_id=candidate_delivery.intent_id AND later.ordinal>candidate_item.ordinal
      AND later.status NOT IN('pending','retry_wait','cancelled'))
   ORDER BY candidate_delivery.updated_at,candidate_item.ordinal,candidate_delivery.intent_id
  FOR UPDATE OF candidate_delivery SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN jsonb_build_object('outcome','empty'); END IF;
  SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries
   WHERE intent_id=candidate_intent_id;
  SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
   WHERE id=candidate_item_id;
  SELECT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts prior_attempt
   JOIN agent_runtime_scheduled_wecom_delivery_items prior_item ON prior_item.id=prior_attempt.item_id
   WHERE prior_item.intent_id=d.intent_id) INTO has_attempts;
  live:=_agent_runtime_scheduled_wecom_live_context(d.intent_id);
  IF live->>'outcome'<>'available' THEN
   IF(NOT has_attempts AND _agent_runtime_scheduled_wecom_cancel_unavailable(
      d.intent_id,COALESCE(live->>'reason_code','wecom_contract_unavailable')))
   OR(has_attempts AND _agent_runtime_scheduled_wecom_terminalize_unavailable_continuation(
      d.intent_id,COALESCE(live->>'reason_code','wecom_contract_unavailable'))) THEN
    CONTINUE;
   END IF;
   excluded:=array_append(excluded,d.intent_id);CONTINUE;
  END IF;
  previous_request_id:=d.claim_request_id;
  token:=gen_random_uuid();
  UPDATE agent_runtime_scheduled_wecom_deliveries SET status='claimed',state_version=state_version+1,
   claim_worker_id=btrim(p_worker_id),claim_request_id=p_claim_request_id,lease_token=token,
   lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),next_attempt_at=NULL,
   terminal_reason_code=NULL,updated_at=clock_timestamp()
   WHERE intent_id=d.intent_id AND state_version=d.state_version
    AND status=d.status AND claim_request_id IS NOT DISTINCT FROM d.claim_request_id
    AND lease_token IS NOT DISTINCT FROM d.lease_token
    AND lease_expires_at IS NOT DISTINCT FROM d.lease_expires_at RETURNING * INTO d;
  IF NOT FOUND THEN CONTINUE; END IF;
  INSERT INTO agent_runtime_scheduled_wecom_continuation_claim_requests(
   request_id,intent_id,item_id,worker_id,claim_kind,lease_seconds,lease_token,lease_expires_at,
   previous_claim_request_id,delivery_state_version,item_state_version)
  VALUES(p_claim_request_id,d.intent_id,item.id,btrim(p_worker_id),
   CASE WHEN has_attempts THEN 'continuation' ELSE 'initial' END,p_lease_seconds,d.lease_token,
   d.lease_expires_at,previous_request_id,d.state_version,item.state_version)
  RETURNING * INTO request;
  RETURN _agent_runtime_scheduled_wecom_continuation_json(request,'claimed');
 END LOOP;
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT'
  USING ERRCODE='55000';
END $$;

COMMENT ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v2(UUID,TEXT,INTEGER) IS
 'Claims an initial or strict next due item only when every existing attempt is terminal accepted/rejected; never recreates or redispatches a historical attempt.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_continuation_claim_immutable(),
 _agent_runtime_scheduled_wecom_continuation_request_guard(),
 _agent_runtime_scheduled_wecom_terminalize_unavailable_continuation(UUID,TEXT),
 _agent_runtime_scheduled_wecom_continuation_json(
  agent_runtime_scheduled_wecom_continuation_claim_requests,TEXT),
 claim_agent_runtime_scheduled_wecom_delivery_v2(UUID,TEXT,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
REVOKE EXECUTE ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v1(UUID,TEXT,INTEGER)
 FROM everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v2(UUID,TEXT,INTEGER)
 TO everydayai_wecom_runtime;

RESET ROLE;
