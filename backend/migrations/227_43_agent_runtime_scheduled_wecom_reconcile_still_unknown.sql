-- 227_43: Durable Scheduled Runtime WeCom still-unknown reconciliation results.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_scheduled_wecom_reconcile_readback_hash(
 p_reconcile_result TEXT,p_readback_type TEXT,p_readback_code TEXT,p_readback_metadata JSONB,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS TEXT
LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(jsonb_build_object(
  'domain','everydayai.scheduled_wecom.reconcile_readback.v1',
  'reconcile_result',p_reconcile_result,'readback_type',p_readback_type,
  'readback_code',p_readback_code,'readback_metadata',p_readback_metadata,
  'provider_request_id',btrim(p_provider_request_id),'idempotency_key',p_idempotency_key,
  'provider_revision',p_provider_revision)),'UTF8'),'sha256'),'hex')
$$;

CREATE TABLE agent_runtime_scheduled_wecom_reconcile_result_requests(
 request_id UUID PRIMARY KEY,
 claim_request_id UUID NOT NULL UNIQUE
  REFERENCES agent_runtime_scheduled_wecom_reconcile_claim_requests(request_id) ON DELETE RESTRICT,
 intent_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 attempt_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_dispatch_attempts(id) ON DELETE RESTRICT,
 reconcile_token UUID NOT NULL,worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 1 AND 128),
 expected_delivery_state_version BIGINT NOT NULL CHECK(expected_delivery_state_version>=1),
 expected_item_state_version BIGINT NOT NULL CHECK(expected_item_state_version>=1),
 provider_request_id TEXT NOT NULL CHECK(length(provider_request_id) BETWEEN 8 AND 200),
 idempotency_key TEXT NOT NULL CHECK(idempotency_key~'^[0-9a-f]{64}$'),
 provider_revision BIGINT NOT NULL CHECK(provider_revision>0),
 reconcile_result TEXT NOT NULL CHECK(reconcile_result='still_unknown'),
 readback_type TEXT NOT NULL CHECK(readback_type IN('wecom_app','wecom_smart_robot')),
 readback_hash TEXT NOT NULL CHECK(readback_hash~'^[0-9a-f]{64}$'),
 readback_code TEXT CHECK(readback_code IS NULL OR readback_code~'^[a-z0-9_]{1,80}$'),
 readback_metadata JSONB NOT NULL CHECK(jsonb_typeof(readback_metadata)='object'
  AND pg_column_size(readback_metadata)<=4096),
 delay_seconds INTEGER NOT NULL CHECK(delay_seconds BETWEEN 5 AND 86400),
 result_delivery_status TEXT NOT NULL CHECK(result_delivery_status='reconcile_required'),
 result_item_status TEXT NOT NULL CHECK(result_item_status='reconcile_required'),
 result_delivery_state_version BIGINT NOT NULL CHECK(result_delivery_state_version>=1),
 result_item_state_version BIGINT NOT NULL CHECK(result_item_state_version>=1),
 next_attempt_at TIMESTAMPTZ NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(request_id,claim_request_id,intent_id,item_id,attempt_id),
 CHECK(_agent_runtime_scheduled_wecom_receipt_metadata_valid(readback_metadata)),
 CHECK(readback_hash=_agent_runtime_scheduled_wecom_reconcile_readback_hash(
  reconcile_result,readback_type,readback_code,readback_metadata,provider_request_id,
  idempotency_key,provider_revision))
);
ALTER TABLE agent_runtime_scheduled_wecom_reconcile_result_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_reconcile_result_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_reconcile_result_requests_owner
 ON agent_runtime_scheduled_wecom_reconcile_result_requests
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_reconcile_result_requests
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_IMMUTABLE'
  USING ERRCODE='55000';
END $$;
CREATE TRIGGER runtime_scheduled_wecom_reconcile_result_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_reconcile_result_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_immutable();

CREATE FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id OR reconcile_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
   WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_wecom_reconcile_result_global_request_guard BEFORE INSERT
 ON agent_runtime_scheduled_wecom_reconcile_result_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_request_guard();

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
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard() RETURNS TRIGGER
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
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT'
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
   WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests
   WHERE request_id=guard_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_json(
 p_request agent_runtime_scheduled_wecom_reconcile_result_requests,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'request_id',p_request.request_id,
  'claim_request_id',p_request.claim_request_id,'intent_id',p_request.intent_id,
  'item_id',p_request.item_id,'attempt_id',p_request.attempt_id,
  'reconcile_result',p_request.reconcile_result,'readback_type',p_request.readback_type,
  'readback_hash',p_request.readback_hash,'readback_code',p_request.readback_code,
  'readback_metadata',p_request.readback_metadata,'delay_seconds',p_request.delay_seconds,
  'next_attempt_at',p_request.next_attempt_at,'attempt_status','unknown',
  'dispatch_phase','ambiguous','item_status',p_request.result_item_status,
  'delivery_status',p_request.result_delivery_status,
  'delivery_state_version',p_request.result_delivery_state_version,
  'item_state_version',p_request.result_item_state_version)
$$;

CREATE FUNCTION record_agent_runtime_scheduled_wecom_reconcile_result_v1(
 p_request_id UUID,p_claim_request_id UUID,p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,
 p_reconcile_token UUID,p_worker_id TEXT,p_expected_delivery_state_version BIGINT,
 p_expected_item_state_version BIGINT,p_provider_request_id TEXT,p_idempotency_key TEXT,
 p_provider_revision BIGINT,p_reconcile_result TEXT,p_readback_type TEXT,p_readback_hash TEXT,
 p_readback_code TEXT,p_readback_metadata JSONB,p_delay_seconds INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 claim agent_runtime_scheduled_wecom_reconcile_claim_requests%ROWTYPE;
 request agent_runtime_scheduled_wecom_reconcile_result_requests%ROWTYPE;
 retry_at TIMESTAMPTZ;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_request_id IS NULL OR p_claim_request_id IS NULL OR p_intent_id IS NULL
 OR p_item_id IS NULL OR p_attempt_id IS NULL OR p_reconcile_token IS NULL
 OR p_worker_id IS NULL OR p_expected_delivery_state_version IS NULL
 OR p_expected_item_state_version IS NULL OR p_provider_request_id IS NULL
 OR p_idempotency_key IS NULL OR p_provider_revision IS NULL OR p_reconcile_result IS NULL
 OR p_readback_type IS NULL OR p_readback_hash IS NULL OR p_readback_metadata IS NULL
 OR p_delay_seconds IS NULL OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 128
 OR length(btrim(p_provider_request_id)) NOT BETWEEN 8 AND 200
 OR p_expected_delivery_state_version<1 OR p_expected_item_state_version<1
 OR p_idempotency_key!~'^[0-9a-f]{64}$' OR p_provider_revision<1
 OR p_reconcile_result<>'still_unknown'
 OR p_readback_type NOT IN('wecom_app','wecom_smart_robot')
 OR p_readback_hash!~'^[0-9a-f]{64}$'
 OR(p_readback_code IS NOT NULL AND p_readback_code!~'^[a-z0-9_]{1,80}$')
 OR NOT _agent_runtime_scheduled_wecom_receipt_metadata_valid(p_readback_metadata)
 OR p_readback_hash IS DISTINCT FROM _agent_runtime_scheduled_wecom_reconcile_readback_hash(
  p_reconcile_result,p_readback_type,p_readback_code,p_readback_metadata,
  p_provider_request_id,p_idempotency_key,p_provider_revision)
 OR p_delay_seconds NOT BETWEEN 5 AND 86400 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_INVALID'
   USING ERRCODE='22023';
 END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(p_request_id);
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_reconcile_result_requests
  WHERE request_id=p_request_id;
 IF FOUND THEN
  IF(request.claim_request_id,request.intent_id,request.item_id,request.attempt_id,
    request.reconcile_token,request.worker_id,request.expected_delivery_state_version,
    request.expected_item_state_version,request.provider_request_id,request.idempotency_key,
    request.provider_revision,request.reconcile_result,request.readback_type,
    request.readback_hash,request.readback_code,request.readback_metadata,request.delay_seconds)
   IS DISTINCT FROM(p_claim_request_id,p_intent_id,p_item_id,p_attempt_id,p_reconcile_token,
    btrim(p_worker_id),p_expected_delivery_state_version,p_expected_item_state_version,
    btrim(p_provider_request_id),p_idempotency_key,p_provider_revision,p_reconcile_result,
    p_readback_type,p_readback_hash,p_readback_code,p_readback_metadata,p_delay_seconds) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT'
    USING ERRCODE='55000';
  END IF;
  RETURN _agent_runtime_scheduled_wecom_reconcile_result_json(request,'readback');
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=p_request_id OR reconcile_request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
   WHERE claim_request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
   WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
   WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
   WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests
   WHERE request_id=p_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 SELECT * INTO claim FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
  WHERE request_id=p_claim_request_id;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries
  WHERE intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items
  WHERE id=p_item_id AND intent_id=p_intent_id FOR UPDATE;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE id=p_attempt_id AND item_id=p_item_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests
   WHERE claim_request_id=p_claim_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 IF claim.request_id IS NULL OR d.intent_id IS NULL OR item.id IS NULL OR a.id IS NULL
 OR(claim.intent_id,claim.item_id,claim.attempt_id,claim.reconcile_token,claim.worker_id,
    claim.delivery_state_version,claim.item_state_version,claim.provider_request_id,
    claim.idempotency_key,claim.provider_revision)
  IS DISTINCT FROM(p_intent_id,p_item_id,p_attempt_id,p_reconcile_token,btrim(p_worker_id),
   p_expected_delivery_state_version,p_expected_item_state_version,btrim(p_provider_request_id),
   p_idempotency_key,p_provider_revision)
 OR(d.reconcile_request_id,d.reconcile_token,d.reconcile_worker_id,d.state_version)
  IS DISTINCT FROM(p_claim_request_id,p_reconcile_token,btrim(p_worker_id),
   p_expected_delivery_state_version)
 OR d.status NOT IN('unknown','reconcile_required')
 OR item.state_version IS DISTINCT FROM p_expected_item_state_version
 OR item.status NOT IN('unknown','reconcile_required')
 OR(a.status,a.dispatch_phase,a.provider_request_id,a.idempotency_key,a.provider_revision)
  IS DISTINCT FROM('unknown','ambiguous',btrim(p_provider_request_id),p_idempotency_key,
   p_provider_revision) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 retry_at:=clock_timestamp()+make_interval(secs=>p_delay_seconds);
 UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='reconcile_required',
  state_version=state_version+1,next_attempt_at=retry_at,
  terminal_reason_code='wecom_dispatch_unknown',updated_at=clock_timestamp()
  WHERE id=item.id AND state_version=p_expected_item_state_version
   AND status IN('unknown','reconcile_required') RETURNING * INTO item;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 UPDATE agent_runtime_scheduled_wecom_deliveries SET status='reconcile_required',
  state_version=state_version+1,reconcile_worker_id=NULL,reconcile_request_id=NULL,
  reconcile_token=NULL,reconcile_lease_expires_at=NULL,next_attempt_at=retry_at,
  terminal_reason_code='wecom_dispatch_unknown',updated_at=clock_timestamp()
  WHERE intent_id=d.intent_id AND state_version=p_expected_delivery_state_version
   AND reconcile_request_id=p_claim_request_id AND reconcile_token=p_reconcile_token
   AND reconcile_worker_id=btrim(p_worker_id)
  RETURNING * INTO d;
 IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_FENCED'
  USING ERRCODE='40001'; END IF;
 INSERT INTO agent_runtime_scheduled_wecom_reconcile_result_requests(
  request_id,claim_request_id,intent_id,item_id,attempt_id,reconcile_token,worker_id,
  expected_delivery_state_version,expected_item_state_version,provider_request_id,
  idempotency_key,provider_revision,reconcile_result,readback_type,readback_hash,
  readback_code,readback_metadata,delay_seconds,result_delivery_status,result_item_status,
  result_delivery_state_version,result_item_state_version,next_attempt_at)
 VALUES(p_request_id,p_claim_request_id,d.intent_id,item.id,a.id,p_reconcile_token,
  btrim(p_worker_id),p_expected_delivery_state_version,p_expected_item_state_version,
  btrim(p_provider_request_id),p_idempotency_key,p_provider_revision,p_reconcile_result,
  p_readback_type,p_readback_hash,p_readback_code,p_readback_metadata,p_delay_seconds,
  d.status,item.status,d.state_version,item.state_version,retry_at) RETURNING * INTO request;
 RETURN _agent_runtime_scheduled_wecom_reconcile_result_json(request,'recorded');
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT'
  USING ERRCODE='55000';
END $$;

COMMENT ON FUNCTION record_agent_runtime_scheduled_wecom_reconcile_result_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,
 TEXT,JSONB,INTEGER) IS
 'Records only a typed still_unknown provider readback, releases reconciliation ownership, and schedules the same frozen attempt for a later reconciliation claim.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_reconcile_readback_hash(
 TEXT,TEXT,TEXT,JSONB,TEXT,TEXT,BIGINT),
 _agent_runtime_scheduled_wecom_reconcile_result_immutable(),
 _agent_runtime_scheduled_wecom_reconcile_result_request_guard(),
 _agent_runtime_scheduled_wecom_reconcile_result_json(
  agent_runtime_scheduled_wecom_reconcile_result_requests,TEXT),
 record_agent_runtime_scheduled_wecom_reconcile_result_v1(
  UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,
  TEXT,JSONB,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION record_agent_runtime_scheduled_wecom_reconcile_result_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,
 TEXT,JSONB,INTEGER) TO everydayai_wecom_runtime;

RESET ROLE;
