-- 227_40: Atomic Scheduled Runtime WeCom dispatch outcomes and durable request readback.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_wecom_outcome_requests(
 request_id UUID PRIMARY KEY,
 attempt_id UUID NOT NULL UNIQUE
  REFERENCES agent_runtime_scheduled_wecom_dispatch_attempts(id) ON DELETE RESTRICT,
 intent_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 claim_request_id UUID NOT NULL,lease_token UUID NOT NULL,
 worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 1 AND 128),
 expected_delivery_state_version BIGINT NOT NULL CHECK(expected_delivery_state_version>=1),
 expected_item_state_version BIGINT NOT NULL CHECK(expected_item_state_version>=1),
 provider_request_id TEXT NOT NULL CHECK(length(provider_request_id) BETWEEN 8 AND 200),
 idempotency_key TEXT NOT NULL CHECK(idempotency_key~'^[0-9a-f]{64}$'),
 provider_revision BIGINT NOT NULL CHECK(provider_revision>0),
 dispatch_outcome TEXT NOT NULL CHECK(dispatch_outcome IN('accepted','rejected','unknown')),
 receipt_type TEXT CHECK(receipt_type IS NULL OR receipt_type~'^[a-z0-9_]{1,80}$'),
 receipt_hash TEXT CHECK(receipt_hash IS NULL OR receipt_hash~'^[0-9a-f]{64}$'),
 receipt_code TEXT CHECK(receipt_code IS NULL OR receipt_code~'^[a-z0-9_]{1,80}$'),
 receipt_metadata JSONB NOT NULL CHECK(jsonb_typeof(receipt_metadata)='object'
  AND pg_column_size(receipt_metadata)<=4096),
 result_item_status TEXT NOT NULL CHECK(result_item_status IN('accepted','failed','unknown')),
 result_delivery_status TEXT NOT NULL CHECK(result_delivery_status IN(
  'claimed','unknown','completed','partial','failed')),
 result_delivery_state_version BIGINT NOT NULL CHECK(result_delivery_state_version>=1),
 result_item_state_version BIGINT NOT NULL CHECK(result_item_state_version>=1),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK((dispatch_outcome='unknown' AND receipt_type IS NULL AND receipt_hash IS NULL
   AND receipt_code IS NULL AND result_item_status='unknown' AND result_delivery_status='unknown')
  OR(dispatch_outcome IN('accepted','rejected') AND receipt_type IS NOT NULL
   AND receipt_hash IS NOT NULL))
);
ALTER TABLE agent_runtime_scheduled_wecom_outcome_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_outcome_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_outcome_requests_owner
 ON agent_runtime_scheduled_wecom_outcome_requests
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_outcome_requests
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_outcome_request_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_OUTCOME_REQUEST_IMMUTABLE'
  USING ERRCODE='55000';
END $$;
CREATE TRIGGER runtime_scheduled_wecom_outcome_request_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_outcome_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_outcome_request_immutable();

CREATE FUNCTION _agent_runtime_scheduled_wecom_receipt_metadata_valid(p_metadata JSONB) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE entry RECORD;value_text TEXT;
BEGIN
 IF p_metadata IS NULL OR jsonb_typeof(p_metadata)<>'object'
 OR pg_column_size(p_metadata)>4096 THEN RETURN FALSE; END IF;
 FOR entry IN SELECT key,value FROM jsonb_each(p_metadata) LOOP
  value_text:=entry.value#>>'{}';
  IF entry.key IN('provider_message_id','trace_id') THEN
   IF jsonb_typeof(entry.value)<>'string' OR length(value_text) NOT BETWEEN 1 AND 200
   OR value_text!~'^[A-Za-z0-9._:-]+$' THEN RETURN FALSE; END IF;
  ELSIF entry.key='provider_code' THEN
   IF jsonb_typeof(entry.value)<>'string' OR value_text!~'^[A-Za-z0-9._:-]{1,80}$' THEN
    RETURN FALSE; END IF;
  ELSIF entry.key='http_status' THEN
   IF jsonb_typeof(entry.value)<>'number' OR value_text!~'^[0-9]{3}$'
   OR value_text::INTEGER NOT BETWEEN 100 AND 599 THEN RETURN FALSE; END IF;
  ELSIF entry.key='wecom_errcode' THEN
   IF jsonb_typeof(entry.value)<>'number' OR value_text!~'^-?[0-9]{1,10}$'
   OR value_text::NUMERIC NOT BETWEEN -2147483648 AND 2147483647 THEN RETURN FALSE; END IF;
  ELSE RETURN FALSE;
  END IF;
 END LOOP;
 RETURN TRUE;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_receipt_hash(
 p_dispatch_outcome TEXT,p_receipt_type TEXT,p_receipt_code TEXT,p_receipt_metadata JSONB,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS TEXT
LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(jsonb_build_object(
  'domain','everydayai.scheduled_wecom.dispatch_receipt.v1',
  'dispatch_outcome',p_dispatch_outcome,'receipt_type',p_receipt_type,
  'receipt_code',p_receipt_code,'receipt_metadata',p_receipt_metadata,
  'provider_request_id',btrim(p_provider_request_id),'idempotency_key',p_idempotency_key,
  'provider_revision',p_provider_revision)),'UTF8'),'sha256'),'hex')
$$;

ALTER TABLE agent_runtime_scheduled_wecom_outcome_requests
 ADD CONSTRAINT runtime_scheduled_wecom_outcome_receipt_typed CHECK(
  _agent_runtime_scheduled_wecom_receipt_metadata_valid(receipt_metadata)
  AND((dispatch_outcome='unknown' AND receipt_metadata='{}'::JSONB)
   OR(dispatch_outcome IN('accepted','rejected')
    AND receipt_type IN('wecom_app','wecom_smart_robot')
    AND receipt_hash=_agent_runtime_scheduled_wecom_receipt_hash(dispatch_outcome,receipt_type,
     receipt_code,receipt_metadata,provider_request_id,idempotency_key,provider_revision)))
 );

CREATE FUNCTION _agent_runtime_scheduled_wecom_outcome_json(
 p_request agent_runtime_scheduled_wecom_outcome_requests,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'request_id',p_request.request_id,
  'intent_id',p_request.intent_id,'item_id',p_request.item_id,'attempt_id',p_request.attempt_id,
  'dispatch_outcome',p_request.dispatch_outcome,'receipt_type',p_request.receipt_type,
  'receipt_hash',p_request.receipt_hash,'receipt_code',p_request.receipt_code,
  'receipt_metadata',p_request.receipt_metadata,'attempt_status',p_request.dispatch_outcome,
  'item_status',p_request.result_item_status,'delivery_status',p_request.result_delivery_status,
  'delivery_state_version',p_request.result_delivery_state_version,
  'item_state_version',p_request.result_item_state_version)
$$;

CREATE FUNCTION record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(
 p_request_id UUID,p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,
 p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_expected_delivery_state_version BIGINT,p_expected_item_state_version BIGINT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT,
 p_dispatch_outcome TEXT,p_receipt_type TEXT,p_receipt_hash TEXT,p_receipt_code TEXT,
 p_receipt_metadata JSONB) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 request agent_runtime_scheduled_wecom_outcome_requests%ROWTYPE;
 has_remaining BOOLEAN;accepted_count INTEGER;item_count INTEGER;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_request_id IS NULL OR p_intent_id IS NULL OR p_item_id IS NULL OR p_attempt_id IS NULL
 OR p_claim_request_id IS NULL OR p_lease_token IS NULL OR p_worker_id IS NULL
 OR p_expected_delivery_state_version IS NULL OR p_expected_item_state_version IS NULL
 OR p_provider_request_id IS NULL OR p_idempotency_key IS NULL OR p_provider_revision IS NULL
 OR p_dispatch_outcome IS NULL OR p_receipt_metadata IS NULL
 OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128
 OR length(btrim(p_provider_request_id)) NOT BETWEEN 8 AND 200
 OR p_idempotency_key!~'^[0-9a-f]{64}$' OR p_provider_revision<1
 OR p_expected_delivery_state_version<1 OR p_expected_item_state_version<1
 OR p_dispatch_outcome NOT IN('accepted','rejected','unknown')
 OR NOT _agent_runtime_scheduled_wecom_receipt_metadata_valid(p_receipt_metadata)
 OR(p_dispatch_outcome='unknown' AND(p_receipt_type IS NOT NULL OR p_receipt_hash IS NOT NULL
  OR p_receipt_code IS NOT NULL OR p_receipt_metadata<>'{}'::JSONB))
 OR(p_dispatch_outcome IN('accepted','rejected') AND(p_receipt_type IS NULL
  OR p_receipt_type NOT IN('wecom_app','wecom_smart_robot') OR p_receipt_hash IS NULL
  OR p_receipt_hash!~'^[0-9a-f]{64}$'
  OR p_receipt_hash IS DISTINCT FROM
   _agent_runtime_scheduled_wecom_receipt_hash(p_dispatch_outcome,p_receipt_type,p_receipt_code,
    p_receipt_metadata,p_provider_request_id,p_idempotency_key,p_provider_revision)
  OR(p_receipt_code IS NOT NULL AND p_receipt_code!~'^[a-z0-9_]{1,80}$'))) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_OUTCOME_INVALID' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-wecom-outcome:'||p_request_id,0));
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_outcome_requests
  WHERE request_id=p_request_id;
 IF FOUND THEN
  IF(request.intent_id,request.item_id,request.attempt_id,request.claim_request_id,
     request.lease_token,request.worker_id,request.expected_delivery_state_version,
     request.expected_item_state_version,request.provider_request_id,request.idempotency_key,
     request.provider_revision,request.dispatch_outcome,request.receipt_type,request.receipt_hash,
     request.receipt_code,request.receipt_metadata)
   IS DISTINCT FROM(p_intent_id,p_item_id,p_attempt_id,p_claim_request_id,p_lease_token,
    btrim(p_worker_id),p_expected_delivery_state_version,p_expected_item_state_version,
    btrim(p_provider_request_id),p_idempotency_key,p_provider_revision,p_dispatch_outcome,
    p_receipt_type,p_receipt_hash,p_receipt_code,p_receipt_metadata) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_OUTCOME_REQUEST_CONFLICT'
    USING ERRCODE='55000';
  END IF;
  RETURN _agent_runtime_scheduled_wecom_outcome_json(request,'readback');
 END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries
  WHERE intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=p_item_id AND intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE id=p_attempt_id AND item_id=p_item_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests r
  WHERE r.attempt_id=p_attempt_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_OUTCOME_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 IF d.intent_id IS NULL OR item.id IS NULL OR a.id IS NULL OR d.status<>'dispatching'
 OR item.status<>'dispatching' OR a.status<>'dispatch_started'
 OR a.dispatch_phase<>'external_request_started'
 OR d.state_version IS DISTINCT FROM p_expected_delivery_state_version
 OR item.state_version IS DISTINCT FROM p_expected_item_state_version
 OR(d.claim_request_id,d.lease_token,d.claim_worker_id)
  IS DISTINCT FROM(p_claim_request_id,p_lease_token,btrim(p_worker_id))
 OR NOT _agent_runtime_scheduled_wecom_attempt_identity_matches(a,p_item_id,
  p_provider_request_id,p_idempotency_key,p_provider_revision)
 OR p_provider_revision IS DISTINCT FROM d.provider_revision
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items earlier
  WHERE earlier.intent_id=item.intent_id AND earlier.ordinal<item.ordinal
   AND earlier.status NOT IN('accepted','failed','cancelled'))
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items later
  WHERE later.intent_id=item.intent_id AND later.ordinal>item.ordinal
   AND later.status NOT IN('pending','retry_wait','cancelled')) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status=p_dispatch_outcome,
  dispatch_phase=CASE WHEN p_dispatch_outcome='unknown' THEN 'ambiguous' ELSE 'receipt_recorded' END,
  receipt_type=p_receipt_type,receipt_hash=p_receipt_hash,receipt_code=p_receipt_code,
  was_ambiguous=p_dispatch_outcome='unknown',
  unknown_at=CASE WHEN p_dispatch_outcome='unknown' THEN clock_timestamp() ELSE NULL END,
  resolved_at=CASE WHEN p_dispatch_outcome<>'unknown' THEN clock_timestamp() ELSE NULL END,
  updated_at=clock_timestamp() WHERE id=a.id RETURNING * INTO a;
 UPDATE agent_runtime_scheduled_wecom_delivery_items SET
  status=CASE p_dispatch_outcome WHEN 'accepted' THEN 'accepted'
   WHEN 'rejected' THEN 'failed' ELSE 'unknown' END,
  state_version=state_version+1,next_attempt_at=NULL,
  terminal_reason_code=CASE p_dispatch_outcome WHEN 'rejected' THEN 'wecom_dispatch_rejected'
   WHEN 'unknown' THEN 'wecom_dispatch_unknown' ELSE NULL END,
  updated_at=clock_timestamp() WHERE id=item.id RETURNING * INTO item;
 IF p_dispatch_outcome='unknown' THEN
  UPDATE agent_runtime_scheduled_wecom_deliveries SET status='unknown',state_version=state_version+1,
   claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL,lease_expires_at=NULL,
   next_attempt_at=NULL,terminal_reason_code='wecom_dispatch_unknown',updated_at=clock_timestamp()
   WHERE intent_id=d.intent_id RETURNING * INTO d;
 ELSE
  SELECT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items remaining
   WHERE remaining.intent_id=d.intent_id AND remaining.status IN('pending','retry_wait'))
   INTO has_remaining;
  IF has_remaining THEN
   UPDATE agent_runtime_scheduled_wecom_deliveries SET status='claimed',state_version=state_version+1,
    next_attempt_at=NULL,terminal_reason_code=NULL,updated_at=clock_timestamp()
    WHERE intent_id=d.intent_id RETURNING * INTO d;
  ELSE
   SELECT count(*) FILTER(WHERE terminal.status='accepted'),count(*) INTO accepted_count,item_count
    FROM agent_runtime_scheduled_wecom_delivery_items terminal WHERE terminal.intent_id=d.intent_id;
   UPDATE agent_runtime_scheduled_wecom_deliveries SET
    status=CASE WHEN accepted_count=item_count THEN 'completed'
     WHEN accepted_count>0 THEN 'partial' ELSE 'failed' END,
    state_version=state_version+1,claim_worker_id=NULL,claim_request_id=NULL,
    lease_token=NULL,lease_expires_at=NULL,next_attempt_at=NULL,
    terminal_reason_code=CASE WHEN accepted_count=item_count THEN NULL
     WHEN accepted_count>0 THEN 'wecom_dispatch_partial' ELSE 'wecom_dispatch_failed' END,
    updated_at=clock_timestamp() WHERE intent_id=d.intent_id RETURNING * INTO d;
  END IF;
 END IF;
 INSERT INTO agent_runtime_scheduled_wecom_outcome_requests(request_id,attempt_id,intent_id,item_id,
  claim_request_id,lease_token,worker_id,expected_delivery_state_version,
  expected_item_state_version,provider_request_id,idempotency_key,provider_revision,
  dispatch_outcome,receipt_type,receipt_hash,receipt_code,receipt_metadata,result_item_status,
  result_delivery_status,result_delivery_state_version,result_item_state_version)
 VALUES(p_request_id,a.id,d.intent_id,item.id,p_claim_request_id,p_lease_token,btrim(p_worker_id),
  p_expected_delivery_state_version,p_expected_item_state_version,btrim(p_provider_request_id),
  p_idempotency_key,p_provider_revision,p_dispatch_outcome,p_receipt_type,p_receipt_hash,
  p_receipt_code,p_receipt_metadata,item.status,d.status,d.state_version,item.state_version)
 RETURNING * INTO request;
 RETURN _agent_runtime_scheduled_wecom_outcome_json(request,'recorded');
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_OUTCOME_REQUEST_CONFLICT' USING ERRCODE='55000';
END $$;

COMMENT ON FUNCTION record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB) IS
 'Atomically records one dispatch_started outcome; durable request readback never repeats a transition.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_receipt_metadata_valid(JSONB),
 _agent_runtime_scheduled_wecom_receipt_hash(TEXT,TEXT,TEXT,JSONB,TEXT,TEXT,BIGINT),
 _agent_runtime_scheduled_wecom_outcome_json(agent_runtime_scheduled_wecom_outcome_requests,TEXT),
 _agent_runtime_scheduled_wecom_outcome_request_immutable(),
 record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(
  UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB)
 TO everydayai_wecom_runtime;

RESET ROLE;
