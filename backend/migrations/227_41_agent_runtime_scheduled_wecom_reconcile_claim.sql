-- 227_41: Durable Scheduled Runtime WeCom UNKNOWN reconciliation claim facts.

SET LOCAL ROLE everydayai_owner;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
  WHERE reconcile_request_id IS NOT NULL OR reconcile_token IS NOT NULL
   OR reconcile_worker_id IS NOT NULL OR reconcile_lease_expires_at IS NOT NULL) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_BACKFILL_REQUIRED'
   USING ERRCODE='55000';
 END IF;
END $$;

CREATE TABLE agent_runtime_scheduled_wecom_reconcile_claim_requests(
 request_id UUID PRIMARY KEY,
 intent_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 attempt_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_dispatch_attempts(id) ON DELETE RESTRICT,
 worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 1 AND 128),
 lease_seconds INTEGER NOT NULL CHECK(lease_seconds BETWEEN 5 AND 900),
 reconcile_token UUID NOT NULL UNIQUE,lease_expires_at TIMESTAMPTZ NOT NULL,
 delivery_state_version BIGINT NOT NULL CHECK(delivery_state_version>=1),
 item_state_version BIGINT NOT NULL CHECK(item_state_version>=1),
 provider_request_id TEXT NOT NULL CHECK(length(provider_request_id) BETWEEN 8 AND 200),
 idempotency_key TEXT NOT NULL CHECK(idempotency_key~'^[0-9a-f]{64}$'),
 provider_revision BIGINT NOT NULL CHECK(provider_revision>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(request_id,intent_id,item_id,attempt_id)
);
ALTER TABLE agent_runtime_scheduled_wecom_reconcile_claim_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_reconcile_claim_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_reconcile_claim_requests_owner
 ON agent_runtime_scheduled_wecom_reconcile_claim_requests
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_reconcile_claim_requests
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_reconcile_claim_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_CLAIM_IMMUTABLE'
  USING ERRCODE='55000';
END $$;
CREATE TRIGGER runtime_scheduled_wecom_reconcile_claim_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_reconcile_claim_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_reconcile_claim_immutable();

CREATE FUNCTION _agent_runtime_scheduled_wecom_reconcile_json(
 p_request agent_runtime_scheduled_wecom_reconcile_claim_requests,
 p_delivery agent_runtime_scheduled_wecom_deliveries,
 p_item agent_runtime_scheduled_wecom_delivery_items,
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'request_id',p_request.request_id,
  'intent_id',p_request.intent_id,'item_id',p_request.item_id,'attempt_id',p_request.attempt_id,
  'worker_id',p_request.worker_id,'reconcile_token',p_request.reconcile_token,
  'lease_seconds',p_request.lease_seconds,
  'lease_expires_at',CASE WHEN p_outcome IN('claimed','renewed','readback')
   THEN p_delivery.reconcile_lease_expires_at ELSE p_request.lease_expires_at END,
  'claimed_lease_expires_at',p_request.lease_expires_at,
  'claim_delivery_state_version',p_request.delivery_state_version,
  'claim_item_state_version',p_request.item_state_version,
  'delivery_state_version',p_delivery.state_version,'item_state_version',p_item.state_version,
  'delivery_status',p_delivery.status,'item_status',p_item.status,
  'attempt_status',p_attempt.status,'dispatch_phase',p_attempt.dispatch_phase,
  'provider_request_id',p_request.provider_request_id,
  'idempotency_key',p_request.idempotency_key,'provider_revision',p_request.provider_revision)
$$;

CREATE FUNCTION claim_agent_runtime_scheduled_wecom_reconcile_v1(
 p_request_id UUID,p_worker_id TEXT,p_lease_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 request agent_runtime_scheduled_wecom_reconcile_claim_requests%ROWTYPE;token UUID;
 candidate_intent_id UUID;candidate_item_id UUID;candidate_attempt_id UUID;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_request_id IS NULL OR p_worker_id IS NULL OR p_lease_seconds IS NULL
 OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128 OR p_lease_seconds NOT BETWEEN 5 AND 900 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_CLAIM_INVALID'
   USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'scheduled-wecom-reconcile-claim:'||p_request_id,0));
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
  WHERE request_id=p_request_id;
 IF FOUND THEN
  IF(request.worker_id,request.lease_seconds)
   IS DISTINCT FROM(btrim(p_worker_id),p_lease_seconds) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
    USING ERRCODE='55000';
  END IF;
  SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=request.intent_id;
  SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=request.item_id;
  SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=request.attempt_id;
  IF(d.reconcile_request_id,d.reconcile_token,d.reconcile_worker_id)
   IS DISTINCT FROM(request.request_id,request.reconcile_token,request.worker_id)
  OR d.reconcile_lease_expires_at<=clock_timestamp() THEN
   RETURN _agent_runtime_scheduled_wecom_reconcile_json(request,d,item,a,'fenced');
  END IF;
  RETURN _agent_runtime_scheduled_wecom_reconcile_json(request,d,item,a,'readback');
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
  WHERE claim_request_id=p_request_id OR reconcile_request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE claim_request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
  WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
  WHERE request_id=p_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 SELECT candidate_delivery.intent_id,candidate_item.id,candidate_attempt.id
  INTO candidate_intent_id,candidate_item_id,candidate_attempt_id
  FROM agent_runtime_scheduled_wecom_deliveries candidate_delivery
  JOIN agent_runtime_scheduled_wecom_delivery_items candidate_item
   ON candidate_item.intent_id=candidate_delivery.intent_id
  JOIN agent_runtime_scheduled_wecom_dispatch_attempts candidate_attempt
   ON candidate_attempt.item_id=candidate_item.id
  WHERE candidate_delivery.status IN('unknown','reconcile_required')
   AND candidate_item.status IN('unknown','reconcile_required')
   AND candidate_attempt.status='unknown' AND candidate_attempt.dispatch_phase='ambiguous'
   AND COALESCE(candidate_delivery.next_attempt_at,'-infinity'::TIMESTAMPTZ)<=clock_timestamp()
   AND(candidate_delivery.reconcile_token IS NULL
    OR candidate_delivery.reconcile_lease_expires_at<=clock_timestamp())
  ORDER BY candidate_attempt.unknown_at,candidate_attempt.id
  FOR UPDATE OF candidate_delivery SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','empty'); END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries
  WHERE intent_id=candidate_intent_id;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=candidate_item_id;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE id=candidate_attempt_id;
 token:=gen_random_uuid();
 UPDATE agent_runtime_scheduled_wecom_deliveries SET state_version=state_version+1,
  reconcile_worker_id=btrim(p_worker_id),reconcile_request_id=p_request_id,
  reconcile_token=token,
  reconcile_lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
  updated_at=clock_timestamp()
  WHERE intent_id=d.intent_id AND status IN('unknown','reconcile_required')
   AND state_version=d.state_version
   AND(reconcile_token IS NULL OR reconcile_lease_expires_at<=clock_timestamp())
  RETURNING * INTO d;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','empty'); END IF;
 INSERT INTO agent_runtime_scheduled_wecom_reconcile_claim_requests(
  request_id,intent_id,item_id,attempt_id,worker_id,lease_seconds,reconcile_token,
  lease_expires_at,delivery_state_version,item_state_version,provider_request_id,
  idempotency_key,provider_revision)
 VALUES(p_request_id,d.intent_id,item.id,a.id,btrim(p_worker_id),p_lease_seconds,d.reconcile_token,
  d.reconcile_lease_expires_at,d.state_version,item.state_version,a.provider_request_id,
  a.idempotency_key,a.provider_revision) RETURNING * INTO request;
 RETURN _agent_runtime_scheduled_wecom_reconcile_json(request,d,item,a,'claimed');
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
  USING ERRCODE='55000';
END $$;

CREATE FUNCTION renew_agent_runtime_scheduled_wecom_reconcile_lease_v1(
 p_intent_id UUID,p_request_id UUID,p_reconcile_token UUID,p_worker_id TEXT,
 p_expected_delivery_state_version BIGINT,p_lease_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 request agent_runtime_scheduled_wecom_reconcile_claim_requests%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_intent_id IS NULL OR p_request_id IS NULL OR p_reconcile_token IS NULL
 OR p_worker_id IS NULL OR p_expected_delivery_state_version IS NULL OR p_lease_seconds IS NULL
 OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128 OR p_expected_delivery_state_version<1
 OR p_lease_seconds NOT BETWEEN 5 AND 900 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RENEW_INVALID'
   USING ERRCODE='22023';
 END IF;
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
  WHERE request_id=p_request_id;
 IF NOT FOUND OR(request.intent_id,request.reconcile_token,request.worker_id)
  IS DISTINCT FROM(p_intent_id,p_reconcile_token,btrim(p_worker_id)) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 UPDATE agent_runtime_scheduled_wecom_deliveries SET state_version=state_version+1,
  reconcile_lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
  updated_at=clock_timestamp()
  WHERE intent_id=p_intent_id AND status IN('unknown','reconcile_required')
   AND reconcile_request_id=p_request_id AND reconcile_token=p_reconcile_token
   AND reconcile_worker_id=btrim(p_worker_id)
   AND state_version=p_expected_delivery_state_version
   AND reconcile_lease_expires_at>clock_timestamp() RETURNING * INTO d;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=request.item_id;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=request.attempt_id;
 RETURN _agent_runtime_scheduled_wecom_reconcile_json(request,d,item,a,'renewed');
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_wecom_reconcile_v1(p_request_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 request agent_runtime_scheduled_wecom_reconcile_claim_requests%ROWTYPE;outcome TEXT;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_request_id IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_READBACK_INVALID'
   USING ERRCODE='22023';
 END IF;
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
  WHERE request_id=p_request_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=request.intent_id;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=request.item_id;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=request.attempt_id;
 outcome:=CASE WHEN(d.reconcile_request_id,d.reconcile_token,d.reconcile_worker_id)
   IS NOT DISTINCT FROM(request.request_id,request.reconcile_token,request.worker_id)
   AND d.reconcile_lease_expires_at>clock_timestamp() THEN 'readback' ELSE 'fenced' END;
 RETURN _agent_runtime_scheduled_wecom_reconcile_json(request,d,item,a,outcome);
END $$;

COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_reconcile_v1(UUID) IS
 'Pure durable request readback; never claims, renews, dispatches, or changes reconciliation facts.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_reconcile_claim_immutable(),
 _agent_runtime_scheduled_wecom_reconcile_json(
  agent_runtime_scheduled_wecom_reconcile_claim_requests,
  agent_runtime_scheduled_wecom_deliveries,agent_runtime_scheduled_wecom_delivery_items,
  agent_runtime_scheduled_wecom_dispatch_attempts,TEXT),
 claim_agent_runtime_scheduled_wecom_reconcile_v1(UUID,TEXT,INTEGER),
 renew_agent_runtime_scheduled_wecom_reconcile_lease_v1(UUID,UUID,UUID,TEXT,BIGINT,INTEGER),
 read_agent_runtime_scheduled_wecom_reconcile_v1(UUID)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_scheduled_wecom_reconcile_v1(UUID,TEXT,INTEGER),
 renew_agent_runtime_scheduled_wecom_reconcile_lease_v1(UUID,UUID,UUID,TEXT,BIGINT,INTEGER),
 read_agent_runtime_scheduled_wecom_reconcile_v1(UUID)
 TO everydayai_wecom_runtime;

RESET ROLE;
