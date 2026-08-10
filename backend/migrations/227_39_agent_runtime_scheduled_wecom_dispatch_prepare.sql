-- 227_39: Fenced Scheduled Runtime WeCom dispatch prepare/start/readback facts.

SET LOCAL ROLE everydayai_owner;

ALTER TABLE agent_runtime_scheduled_wecom_dispatch_attempts
 ADD COLUMN claim_request_id UUID,
 ADD COLUMN lease_token UUID,
 ADD COLUMN claim_worker_id TEXT CHECK(
  claim_worker_id IS NULL OR length(claim_worker_id) BETWEEN 1 AND 128),
 ADD COLUMN prepared_delivery_state_version BIGINT CHECK(prepared_delivery_state_version>=1),
 ADD COLUMN prepared_item_state_version BIGINT CHECK(prepared_item_state_version>=0);

CREATE FUNCTION _agent_runtime_scheduled_wecom_attempt_json(
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'attempt_id',p_attempt.id,
  'item_id',p_attempt.item_id,'attempt_number',p_attempt.attempt_number,
  'provider_request_id',p_attempt.provider_request_id,'idempotency_key',p_attempt.idempotency_key,
  'provider_revision',p_attempt.provider_revision,'status',p_attempt.status)
$$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_attempt_matches(
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_item_id UUID,
 p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT(p_attempt.item_id,p_attempt.claim_request_id,p_attempt.lease_token,p_attempt.claim_worker_id,
  p_attempt.provider_request_id,p_attempt.idempotency_key,p_attempt.provider_revision)
 IS NOT DISTINCT FROM(p_item_id,p_claim_request_id,p_lease_token,btrim(p_worker_id),
  btrim(p_provider_request_id),p_idempotency_key,p_provider_revision)
$$;

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
 OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR length(btrim(COALESCE(p_provider_request_id,''))) NOT BETWEEN 8 AND 200
 OR COALESCE(p_idempotency_key,'')!~'^[0-9a-f]{64}$' OR p_provider_revision<1
 OR p_expected_delivery_state_version<1 OR p_expected_item_state_version<0 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARE_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO provider_hit FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE provider_request_id=btrim(p_provider_request_id) FOR UPDATE;
 provider_found:=FOUND;
 SELECT * INTO idempotency_hit FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE idempotency_key=p_idempotency_key FOR UPDATE;
 idempotency_found:=FOUND;
 IF provider_found AND idempotency_found
 AND provider_hit.id<>idempotency_hit.id THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_PREPARE_CONFLICT' USING ERRCODE='55000';
 END IF;
 IF provider_found THEN a:=provider_hit; ELSIF idempotency_found THEN a:=idempotency_hit; END IF;
 IF provider_found OR idempotency_found THEN
  IF NOT _agent_runtime_scheduled_wecom_attempt_matches(a,p_item_id,p_claim_request_id,p_lease_token,
   p_worker_id,p_provider_request_id,p_idempotency_key,p_provider_revision)
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
 IF NOT FOUND OR item.state_version<>p_expected_item_state_version
 OR item.status NOT IN('pending','retry_wait')
 OR COALESCE(item.next_attempt_at,'-infinity'::TIMESTAMPTZ)>clock_timestamp()
 OR p_provider_revision<>(context->>'provider_revision')::BIGINT
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
 OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR length(btrim(COALESCE(p_provider_request_id,''))) NOT BETWEEN 8 AND 200
 OR COALESCE(p_idempotency_key,'')!~'^[0-9a-f]{64}$' OR p_provider_revision<1
 OR p_expected_delivery_state_version<1 OR p_expected_item_state_version<1 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_START_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=p_item_id AND intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE id=p_attempt_id AND item_id=p_item_id FOR UPDATE;
 IF NOT FOUND OR NOT _agent_runtime_scheduled_wecom_attempt_matches(a,p_item_id,p_claim_request_id,
  p_lease_token,p_worker_id,p_provider_request_id,p_idempotency_key,p_provider_revision) THEN
  RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF d.claim_request_id IS DISTINCT FROM p_claim_request_id OR d.lease_token IS DISTINCT FROM p_lease_token
 OR d.claim_worker_id IS DISTINCT FROM btrim(p_worker_id) OR d.lease_expires_at<=clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF a.status<>'prepared' THEN RETURN _agent_runtime_scheduled_wecom_attempt_json(a,'readback'); END IF;
 IF d.state_version<>p_expected_delivery_state_version OR item.state_version<>p_expected_item_state_version
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
 SELECT attempt.* INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts attempt
  JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=attempt.item_id
  WHERE attempt.id=p_attempt_id AND attempt.item_id=p_item_id AND item.intent_id=p_intent_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id;
 IF NOT _agent_runtime_scheduled_wecom_attempt_matches(a,p_item_id,p_claim_request_id,p_lease_token,
  p_worker_id,p_provider_request_id,p_idempotency_key,p_provider_revision)
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
COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(
 UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT) IS
 'Pure attempt identity readback; never renews a lease or changes delivery facts.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_attempt_json(
 agent_runtime_scheduled_wecom_dispatch_attempts,TEXT),
 _agent_runtime_scheduled_wecom_attempt_matches(
 agent_runtime_scheduled_wecom_dispatch_attempts,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT),
 prepare_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
 prepare_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 TO everydayai_wecom_runtime;

RESET ROLE;
