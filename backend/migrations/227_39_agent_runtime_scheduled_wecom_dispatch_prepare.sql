-- 227_39: Fenced Scheduled Runtime WeCom dispatch prepare/start/readback facts.

SET LOCAL ROLE everydayai_owner;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_DISPATCH_BACKFILL_REQUIRED'
   USING ERRCODE='55000';
 END IF;
END $$;

ALTER TABLE agent_runtime_scheduled_wecom_dispatch_attempts
 ADD COLUMN claim_request_id UUID NOT NULL,
 ADD COLUMN lease_token UUID NOT NULL,
 ADD COLUMN claim_worker_id TEXT NOT NULL CHECK(length(claim_worker_id) BETWEEN 1 AND 128),
 ADD COLUMN prepared_delivery_state_version BIGINT NOT NULL CHECK(prepared_delivery_state_version>=1),
 ADD COLUMN prepared_item_state_version BIGINT NOT NULL CHECK(prepared_item_state_version>=0);

CREATE TABLE agent_runtime_scheduled_wecom_prepared_recovery_requests(
 request_id UUID PRIMARY KEY,
 attempt_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_dispatch_attempts(id) ON DELETE RESTRICT,
 intent_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 1 AND 128),
 lease_seconds INTEGER NOT NULL CHECK(lease_seconds BETWEEN 5 AND 900),
 lease_token UUID NOT NULL UNIQUE,lease_expires_at TIMESTAMPTZ NOT NULL,
 delivery_state_version BIGINT NOT NULL CHECK(delivery_state_version>=1),
 item_state_version BIGINT NOT NULL CHECK(item_state_version>=1),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(request_id,attempt_id,intent_id,item_id)
);
ALTER TABLE agent_runtime_scheduled_wecom_prepared_recovery_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_prepared_recovery_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_prepared_recovery_owner
 ON agent_runtime_scheduled_wecom_prepared_recovery_requests
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_prepared_recovery_requests
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_recovery_request_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECOVERY_REQUEST_IMMUTABLE'
  USING ERRCODE='55000';
END $$;
CREATE TRIGGER runtime_scheduled_wecom_recovery_request_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_prepared_recovery_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_recovery_request_immutable();

CREATE FUNCTION _agent_runtime_scheduled_wecom_attempt_json(
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'attempt_id',p_attempt.id,
  'item_id',p_attempt.item_id,'attempt_number',p_attempt.attempt_number,
  'provider_request_id',p_attempt.provider_request_id,'idempotency_key',p_attempt.idempotency_key,
  'provider_revision',p_attempt.provider_revision,'status',p_attempt.status)
$$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_attempt_identity_matches(
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_item_id UUID,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT(p_attempt.item_id,p_attempt.provider_request_id,p_attempt.idempotency_key,
  p_attempt.provider_revision) IS NOT DISTINCT FROM(p_item_id,btrim(p_provider_request_id),
  p_idempotency_key,p_provider_revision)
$$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_recovery_json(
 p_request agent_runtime_scheduled_wecom_prepared_recovery_requests,
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT _agent_runtime_scheduled_wecom_attempt_json(p_attempt,p_outcome)||jsonb_build_object(
  'intent_id',p_request.intent_id,'claim_request_id',p_request.request_id,
  'worker_id',p_request.worker_id,'lease_token',p_request.lease_token,
  'lease_expires_at',p_request.lease_expires_at,
  'delivery_state_version',p_request.delivery_state_version,
  'item_state_version',p_request.item_state_version)
$$;

CREATE FUNCTION recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1(
 p_recovery_request_id UUID,p_worker_id TEXT,p_lease_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 request agent_runtime_scheduled_wecom_prepared_recovery_requests%ROWTYPE;token UUID;
 candidate_intent_id UUID;candidate_item_id UUID;candidate_attempt_id UUID;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_recovery_request_id IS NULL OR p_worker_id IS NULL OR p_lease_seconds IS NULL
 OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128 OR p_lease_seconds NOT BETWEEN 5 AND 900 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECOVERY_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
  'scheduled-wecom-prepared-recovery:'||p_recovery_request_id,0));
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
  WHERE request_id=p_recovery_request_id;
 IF FOUND THEN
  IF(request.worker_id,request.lease_seconds)
   IS DISTINCT FROM(btrim(p_worker_id),p_lease_seconds) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECOVERY_REQUEST_CONFLICT'
    USING ERRCODE='55000'; END IF;
  SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=request.attempt_id;
  SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=request.intent_id;
  IF(d.claim_request_id,d.lease_token,d.claim_worker_id)
   IS DISTINCT FROM(request.request_id,request.lease_token,request.worker_id)
  OR d.lease_expires_at<=clock_timestamp() OR d.status NOT IN('claimed','dispatching') THEN
   RETURN _agent_runtime_scheduled_wecom_recovery_json(request,a,'fenced');
  END IF;
  RETURN _agent_runtime_scheduled_wecom_recovery_json(request,a,'readback');
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
  WHERE claim_request_id=p_recovery_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE claim_request_id=p_recovery_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECOVERY_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 SELECT candidate_delivery.intent_id,candidate_item.id,candidate_attempt.id
  INTO candidate_intent_id,candidate_item_id,candidate_attempt_id
  FROM agent_runtime_scheduled_wecom_deliveries candidate_delivery
  JOIN agent_runtime_scheduled_wecom_delivery_items candidate_item
   ON candidate_item.intent_id=candidate_delivery.intent_id
  JOIN agent_runtime_scheduled_wecom_dispatch_attempts candidate_attempt
   ON candidate_attempt.item_id=candidate_item.id
  WHERE candidate_delivery.status='claimed'
   AND candidate_delivery.lease_token IS NOT NULL
   AND candidate_delivery.lease_expires_at<=clock_timestamp()
   AND candidate_item.status='dispatching'
   AND candidate_attempt.status='prepared' AND candidate_attempt.dispatch_phase='prepared'
   AND candidate_attempt.dispatch_started_at IS NULL AND candidate_attempt.unknown_at IS NULL
   AND candidate_attempt.resolved_at IS NULL AND candidate_attempt.receipt_type IS NULL
   AND candidate_attempt.receipt_hash IS NULL AND candidate_attempt.receipt_code IS NULL
   AND NOT candidate_attempt.was_ambiguous
 ORDER BY candidate_attempt.prepared_at,candidate_attempt.id
  FOR UPDATE OF candidate_delivery,candidate_item,candidate_attempt SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','empty'); END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries
  WHERE intent_id=candidate_intent_id;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=candidate_item_id;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE id=candidate_attempt_id;
 token:=gen_random_uuid();
 UPDATE agent_runtime_scheduled_wecom_deliveries SET state_version=state_version+1,
  claim_worker_id=btrim(p_worker_id),claim_request_id=p_recovery_request_id,lease_token=token,
  lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),updated_at=clock_timestamp()
  WHERE intent_id=d.intent_id AND status='claimed'
   AND state_version IS NOT DISTINCT FROM d.state_version
   AND claim_request_id IS NOT DISTINCT FROM d.claim_request_id
   AND lease_token IS NOT DISTINCT FROM d.lease_token
   AND claim_worker_id IS NOT DISTINCT FROM d.claim_worker_id
   AND lease_expires_at IS NOT DISTINCT FROM d.lease_expires_at
   AND lease_expires_at<=clock_timestamp() RETURNING * INTO d;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','empty'); END IF;
 INSERT INTO agent_runtime_scheduled_wecom_prepared_recovery_requests(
  request_id,attempt_id,intent_id,item_id,worker_id,lease_seconds,lease_token,lease_expires_at,
  delivery_state_version,item_state_version)
 VALUES(p_recovery_request_id,a.id,d.intent_id,item.id,btrim(p_worker_id),p_lease_seconds,
  d.lease_token,d.lease_expires_at,d.state_version,item.state_version) RETURNING * INTO request;
 RETURN _agent_runtime_scheduled_wecom_recovery_json(request,a,'recovered');
END $$;

CREATE FUNCTION prepare_agent_runtime_scheduled_wecom_dispatch_v1(
 p_intent_id UUID,p_item_id UUID,p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_expected_delivery_state_version BIGINT,p_expected_item_state_version BIGINT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 provider_hit agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 idempotency_hit agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 provider_found BOOLEAN;idempotency_found BOOLEAN;context JSONB;attempt_number INTEGER;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_intent_id IS NULL OR p_item_id IS NULL OR p_claim_request_id IS NULL OR p_lease_token IS NULL
 OR p_worker_id IS NULL OR p_provider_request_id IS NULL OR p_idempotency_key IS NULL
 OR p_provider_revision IS NULL OR p_expected_delivery_state_version IS NULL
 OR p_expected_item_state_version IS NULL
 OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128
 OR length(btrim(p_provider_request_id)) NOT BETWEEN 8 AND 200
 OR p_idempotency_key!~'^[0-9a-f]{64}$' OR p_provider_revision<1
 OR p_expected_delivery_state_version<1 OR p_expected_item_state_version<0 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARE_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO provider_hit FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE provider_request_id=btrim(p_provider_request_id);
 provider_found:=FOUND;
 SELECT * INTO idempotency_hit FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE idempotency_key=p_idempotency_key;
 idempotency_found:=FOUND;
 IF provider_found AND idempotency_found
 AND provider_hit.id<>idempotency_hit.id THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARE_CONFLICT' USING ERRCODE='55000';
 END IF;
 IF provider_found THEN a:=provider_hit; ELSIF idempotency_found THEN a:=idempotency_hit; END IF;
 IF provider_found OR idempotency_found THEN
  IF NOT _agent_runtime_scheduled_wecom_attempt_identity_matches(a,p_item_id,
   p_provider_request_id,p_idempotency_key,p_provider_revision)
  OR(a.claim_request_id,a.lease_token,a.claim_worker_id)
   IS DISTINCT FROM(p_claim_request_id,p_lease_token,btrim(p_worker_id))
  OR(a.prepared_delivery_state_version,a.prepared_item_state_version)
   IS DISTINCT FROM(p_expected_delivery_state_version,p_expected_item_state_version) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARE_CONFLICT' USING ERRCODE='55000'; END IF;
  IF d.claim_request_id IS DISTINCT FROM p_claim_request_id OR d.lease_token IS DISTINCT FROM p_lease_token
  OR d.claim_worker_id IS DISTINCT FROM btrim(p_worker_id) OR d.lease_expires_at<=clock_timestamp() THEN
   RETURN jsonb_build_object('outcome','fenced'); END IF;
  RETURN _agent_runtime_scheduled_wecom_attempt_json(a,'readback');
 END IF;
 context:=read_agent_runtime_scheduled_wecom_dispatch_context_v1(p_intent_id,p_claim_request_id,
  p_lease_token,p_worker_id,p_expected_delivery_state_version);
 IF context->>'outcome'<>'context' THEN RETURN context; END IF;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=p_item_id AND intent_id=p_intent_id FOR UPDATE;
 IF NOT FOUND OR item.state_version IS DISTINCT FROM p_expected_item_state_version
 OR item.status NOT IN('pending','retry_wait')
 OR COALESCE(item.next_attempt_at,'-infinity'::TIMESTAMPTZ)>clock_timestamp()
 OR p_provider_revision IS DISTINCT FROM(context->>'provider_revision')::BIGINT
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items earlier
  WHERE earlier.intent_id=item.intent_id AND earlier.ordinal<item.ordinal
   AND earlier.status NOT IN('accepted','failed','cancelled')) THEN
  RETURN jsonb_build_object('outcome','fenced'); END IF;
 SELECT COALESCE(max(prior.attempt_number),0)+1 INTO attempt_number
  FROM agent_runtime_scheduled_wecom_dispatch_attempts prior WHERE prior.item_id=item.id;
 INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts(item_id,attempt_number,provider_request_id,
  idempotency_key,provider_revision,status,dispatch_phase,claim_request_id,lease_token,claim_worker_id,
  prepared_delivery_state_version,prepared_item_state_version)
 VALUES(item.id,attempt_number,btrim(p_provider_request_id),p_idempotency_key,p_provider_revision,
  'prepared','prepared',p_claim_request_id,p_lease_token,btrim(p_worker_id),
  p_expected_delivery_state_version,p_expected_item_state_version) RETURNING * INTO a;
 UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='dispatching',state_version=state_version+1,
  next_attempt_at=NULL,terminal_reason_code=NULL,updated_at=clock_timestamp() WHERE id=item.id;
 UPDATE agent_runtime_scheduled_wecom_deliveries SET state_version=state_version+1,
  updated_at=clock_timestamp() WHERE intent_id=p_intent_id;
 RETURN _agent_runtime_scheduled_wecom_attempt_json(a,'prepared');
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARE_CONFLICT' USING ERRCODE='55000';
END $$;

CREATE FUNCTION start_agent_runtime_scheduled_wecom_dispatch_v1(
 p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,p_claim_request_id UUID,p_lease_token UUID,
 p_worker_id TEXT,p_expected_delivery_state_version BIGINT,p_expected_item_state_version BIGINT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;context JSONB;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_intent_id IS NULL OR p_item_id IS NULL OR p_attempt_id IS NULL
 OR p_claim_request_id IS NULL OR p_lease_token IS NULL
 OR p_worker_id IS NULL OR p_provider_request_id IS NULL OR p_idempotency_key IS NULL
 OR p_provider_revision IS NULL OR p_expected_delivery_state_version IS NULL
 OR p_expected_item_state_version IS NULL
 OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128
 OR length(btrim(p_provider_request_id)) NOT BETWEEN 8 AND 200
 OR p_idempotency_key!~'^[0-9a-f]{64}$' OR p_provider_revision<1
 OR p_expected_delivery_state_version<1 OR p_expected_item_state_version<1 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_START_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=p_item_id AND intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE id=p_attempt_id AND item_id=p_item_id FOR UPDATE;
 IF d.intent_id IS NULL OR item.id IS NULL OR a.id IS NULL
 OR NOT _agent_runtime_scheduled_wecom_attempt_identity_matches(a,p_item_id,
  p_provider_request_id,p_idempotency_key,p_provider_revision) THEN
  RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF d.claim_request_id IS DISTINCT FROM p_claim_request_id OR d.lease_token IS DISTINCT FROM p_lease_token
 OR d.claim_worker_id IS DISTINCT FROM btrim(p_worker_id) OR d.lease_expires_at<=clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF a.status<>'prepared' THEN RETURN _agent_runtime_scheduled_wecom_attempt_json(a,'readback'); END IF;
 IF d.state_version IS DISTINCT FROM p_expected_delivery_state_version
 OR item.state_version IS DISTINCT FROM p_expected_item_state_version
 OR item.status<>'dispatching' THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 context:=read_agent_runtime_scheduled_wecom_dispatch_context_v1(p_intent_id,p_claim_request_id,
  p_lease_token,p_worker_id,p_expected_delivery_state_version);
 IF context->>'outcome'<>'context' THEN RETURN context; END IF;
 UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='dispatch_started',
  dispatch_phase='external_request_started',dispatch_started_at=clock_timestamp(),
  updated_at=clock_timestamp() WHERE id=a.id RETURNING * INTO a;
 UPDATE agent_runtime_scheduled_wecom_delivery_items SET state_version=state_version+1,
  updated_at=clock_timestamp() WHERE id=item.id;
 UPDATE agent_runtime_scheduled_wecom_deliveries SET status='dispatching',state_version=state_version+1,
  updated_at=clock_timestamp() WHERE intent_id=p_intent_id;
 RETURN _agent_runtime_scheduled_wecom_attempt_json(a,'dispatch_started');
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(
 p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,p_claim_request_id UUID,p_lease_token UUID,
 p_worker_id TEXT,p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_intent_id IS NULL OR p_item_id IS NULL OR p_attempt_id IS NULL
 OR p_claim_request_id IS NULL OR p_lease_token IS NULL OR p_worker_id IS NULL
 OR p_provider_request_id IS NULL OR p_idempotency_key IS NULL OR p_provider_revision IS NULL
 OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128
 OR length(btrim(p_provider_request_id)) NOT BETWEEN 8 AND 200
 OR p_idempotency_key!~'^[0-9a-f]{64}$' OR p_provider_revision<1 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_ATTEMPT_READBACK_INVALID'
   USING ERRCODE='22023'; END IF;
 SELECT attempt.* INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts attempt
  JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=attempt.item_id
  WHERE attempt.id=p_attempt_id AND attempt.item_id=p_item_id AND item.intent_id=p_intent_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id;
 IF NOT _agent_runtime_scheduled_wecom_attempt_identity_matches(a,p_item_id,
  p_provider_request_id,p_idempotency_key,p_provider_revision)
 OR d.claim_request_id IS DISTINCT FROM p_claim_request_id
 OR d.lease_token IS DISTINCT FROM p_lease_token
 OR d.claim_worker_id IS DISTINCT FROM btrim(p_worker_id)
 OR d.lease_expires_at<=clock_timestamp() OR d.status NOT IN('claimed','dispatching') THEN
  RETURN jsonb_build_object('outcome','fenced'); END IF;
 RETURN _agent_runtime_scheduled_wecom_attempt_json(a,'readback');
END $$;

COMMENT ON FUNCTION start_agent_runtime_scheduled_wecom_dispatch_v1(
 UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT) IS
 'The transport may start only after this CAS returns dispatch_started.';
COMMENT ON FUNCTION recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1(UUID,TEXT,INTEGER) IS
 'Recovers only an evidence-free prepared attempt by replacing the expired current delivery claim.';
COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(
 UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT) IS
 'Pure attempt identity readback; never renews a lease or changes delivery facts.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_attempt_json(
 agent_runtime_scheduled_wecom_dispatch_attempts,TEXT),
 _agent_runtime_scheduled_wecom_attempt_identity_matches(
 agent_runtime_scheduled_wecom_dispatch_attempts,UUID,TEXT,TEXT,BIGINT),
 _agent_runtime_scheduled_wecom_recovery_request_immutable(),
 _agent_runtime_scheduled_wecom_recovery_json(
 agent_runtime_scheduled_wecom_prepared_recovery_requests,
 agent_runtime_scheduled_wecom_dispatch_attempts,TEXT),
 recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1(UUID,TEXT,INTEGER),
 prepare_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
 recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1(UUID,TEXT,INTEGER),
 prepare_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 TO everydayai_wecom_runtime;

RESET ROLE;
