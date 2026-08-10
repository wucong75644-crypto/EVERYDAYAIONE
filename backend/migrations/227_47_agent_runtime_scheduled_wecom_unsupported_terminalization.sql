-- 227_47: Durable terminalization of unsupported Scheduled Runtime WeCom items.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_wecom_unsupported_requests(
 request_id UUID PRIMARY KEY,
 intent_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 claim_request_id UUID NOT NULL UNIQUE
  REFERENCES agent_runtime_scheduled_wecom_continuation_claim_requests(request_id) ON DELETE RESTRICT,
 lease_token UUID NOT NULL,
 worker_id TEXT NOT NULL CHECK(length(worker_id) BETWEEN 1 AND 128),
 expected_delivery_state_version BIGINT NOT NULL CHECK(expected_delivery_state_version>=1),
 expected_item_state_version BIGINT NOT NULL CHECK(expected_item_state_version>=0),
 reason_code TEXT NOT NULL CHECK(reason_code IN(
  'wecom_artifact_identity_unsupported','wecom_failed_content_unsupported',
  'wecom_cancelled_content_unsupported','wecom_non_completed_content_unsupported')),
 result_item_status TEXT NOT NULL CHECK(result_item_status='cancelled'),
 result_delivery_status TEXT NOT NULL CHECK(result_delivery_status IN(
  'pending','completed','partial','failed')),
 result_delivery_state_version BIGINT NOT NULL CHECK(result_delivery_state_version>=1),
 result_item_state_version BIGINT NOT NULL CHECK(result_item_state_version>=1),
 terminalized_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(request_id,intent_id,item_id)
);
ALTER TABLE agent_runtime_scheduled_wecom_unsupported_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_unsupported_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_unsupported_owner
 ON agent_runtime_scheduled_wecom_unsupported_requests
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_unsupported_requests
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_unsupported_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_UNSUPPORTED_IMMUTABLE' USING ERRCODE='55000';
END $$;
CREATE TRIGGER runtime_scheduled_wecom_unsupported_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_unsupported_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_unsupported_immutable();

CREATE FUNCTION _agent_runtime_scheduled_wecom_unsupported_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id OR reconcile_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_UNSUPPORTED_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_wecom_unsupported_global_request_guard BEFORE INSERT
 ON agent_runtime_scheduled_wecom_unsupported_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_unsupported_request_guard();

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items item
   WHERE(item.id,item.intent_id)=(NEW.item_id,NEW.intent_id)) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_IDENTITY_INVALID' USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries d
   WHERE d.claim_request_id=NEW.request_id
    AND(d.intent_id,d.claim_worker_id,d.lease_token,d.lease_expires_at,d.state_version)
     IS DISTINCT FROM(NEW.intent_id,NEW.worker_id,NEW.lease_token,NEW.lease_expires_at,
      NEW.delivery_state_version))
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id OR reconcile_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_definitive_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id OR reconcile_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_DEFINITIVE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_legacy_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE guard_request_id UUID;
BEGIN
 IF TG_TABLE_NAME='agent_runtime_scheduled_wecom_deliveries' THEN
  IF NEW.claim_request_id IS NULL OR NEW.claim_request_id IS NOT DISTINCT FROM OLD.claim_request_id THEN RETURN NEW; END IF;
  guard_request_id:=NEW.claim_request_id;
 ELSE guard_request_id:=NEW.request_id;
 END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(guard_request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=guard_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_unsupported_json(
 p_request agent_runtime_scheduled_wecom_unsupported_requests,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'request_id',p_request.request_id,
  'intent_id',p_request.intent_id,'item_id',p_request.item_id,
  'reason_code',p_request.reason_code,'item_status',p_request.result_item_status,
  'delivery_status',p_request.result_delivery_status,
  'delivery_state_version',p_request.result_delivery_state_version,
  'item_state_version',p_request.result_item_state_version,
  'terminalized_at',p_request.terminalized_at)
$$;

CREATE FUNCTION terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1(
 p_request_id UUID,p_intent_id UUID,p_item_id UUID,p_claim_request_id UUID,
 p_lease_token UUID,p_worker_id TEXT,p_expected_delivery_state_version BIGINT,
 p_expected_item_state_version BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 claim agent_runtime_scheduled_wecom_continuation_claim_requests%ROWTYPE;
 request agent_runtime_scheduled_wecom_unsupported_requests%ROWTYPE;
 gate JSONB;reason TEXT;has_remaining BOOLEAN;accepted_count INTEGER;item_count INTEGER;
 terminalized TIMESTAMPTZ;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_request_id IS NULL OR p_intent_id IS NULL OR p_item_id IS NULL
 OR p_claim_request_id IS NULL OR p_lease_token IS NULL
 OR length(btrim(COALESCE(p_worker_id,''))) NOT BETWEEN 1 AND 128
 OR p_expected_delivery_state_version IS NULL OR p_expected_delivery_state_version<1
 OR p_expected_item_state_version IS NULL OR p_expected_item_state_version<0 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_UNSUPPORTED_INVALID' USING ERRCODE='22023';
 END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(p_request_id);
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=p_request_id;
 IF FOUND THEN
  IF(request.intent_id,request.item_id,request.claim_request_id,request.lease_token,request.worker_id,
    request.expected_delivery_state_version,request.expected_item_state_version)
   IS DISTINCT FROM(p_intent_id,p_item_id,p_claim_request_id,p_lease_token,btrim(p_worker_id),
    p_expected_delivery_state_version,p_expected_item_state_version) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_UNSUPPORTED_REQUEST_CONFLICT' USING ERRCODE='55000';
  END IF;
  RETURN _agent_runtime_scheduled_wecom_unsupported_json(request,'readback');
 END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=p_intent_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=p_item_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO claim FROM agent_runtime_scheduled_wecom_continuation_claim_requests
  WHERE request_id=p_claim_request_id;
 IF item.intent_id IS DISTINCT FROM p_intent_id OR claim.request_id IS NULL
 OR(claim.intent_id,claim.item_id,claim.worker_id,claim.lease_token,
    claim.delivery_state_version,claim.item_state_version)
   IS DISTINCT FROM(p_intent_id,p_item_id,btrim(p_worker_id),p_lease_token,
    p_expected_delivery_state_version,p_expected_item_state_version)
 OR(d.status,d.claim_request_id,d.lease_token,d.claim_worker_id,d.state_version)
   IS DISTINCT FROM('claimed',p_claim_request_id,p_lease_token,btrim(p_worker_id),
    p_expected_delivery_state_version)
 OR d.lease_expires_at<=clock_timestamp()
 OR item.state_version IS DISTINCT FROM p_expected_item_state_version
 OR item.status NOT IN('pending','retry_wait')
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts a WHERE a.item_id=item.id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items earlier
  WHERE earlier.intent_id=item.intent_id AND earlier.ordinal<item.ordinal
   AND earlier.status NOT IN('accepted','failed','cancelled'))
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items later
  WHERE later.intent_id=item.intent_id AND later.ordinal>item.ordinal
   AND later.status NOT IN('pending','retry_wait','cancelled')) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 gate:=read_agent_runtime_scheduled_wecom_dispatch_payload_v1(p_intent_id,p_item_id,
  p_claim_request_id,p_lease_token,btrim(p_worker_id),p_expected_delivery_state_version,
  p_expected_item_state_version);
 reason:=gate->>'reason_code';
 IF gate->>'outcome'<>'unsupported' OR reason NOT IN(
  'wecom_artifact_identity_unsupported','wecom_failed_content_unsupported',
  'wecom_cancelled_content_unsupported','wecom_non_completed_content_unsupported') THEN
  RETURN jsonb_build_object('outcome',CASE WHEN gate->>'outcome'='not_found' THEN 'not_found' ELSE 'fenced' END);
 END IF;
 terminalized:=clock_timestamp();
 UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='cancelled',
  state_version=state_version+1,next_attempt_at=NULL,terminal_reason_code=reason,updated_at=terminalized
  WHERE id=item.id AND state_version=item.state_version RETURNING * INTO item;
 SELECT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items remaining
  WHERE remaining.intent_id=d.intent_id AND remaining.status IN('pending','retry_wait')) INTO has_remaining;
 IF has_remaining THEN
  UPDATE agent_runtime_scheduled_wecom_deliveries SET status='pending',state_version=state_version+1,
   claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL,lease_expires_at=NULL,
   reconcile_worker_id=NULL,reconcile_request_id=NULL,reconcile_token=NULL,reconcile_lease_expires_at=NULL,
   next_attempt_at=NULL,terminal_reason_code=NULL,updated_at=terminalized
   WHERE intent_id=d.intent_id AND state_version=d.state_version RETURNING * INTO d;
 ELSE
  SELECT count(*) FILTER(WHERE terminal.status='accepted'),count(*) INTO accepted_count,item_count
   FROM agent_runtime_scheduled_wecom_delivery_items terminal WHERE terminal.intent_id=d.intent_id;
  UPDATE agent_runtime_scheduled_wecom_deliveries SET
   status=CASE WHEN accepted_count=item_count THEN 'completed' WHEN accepted_count>0 THEN 'partial' ELSE 'failed' END,
   state_version=state_version+1,claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL,
   lease_expires_at=NULL,reconcile_worker_id=NULL,reconcile_request_id=NULL,reconcile_token=NULL,
   reconcile_lease_expires_at=NULL,next_attempt_at=NULL,
   terminal_reason_code=CASE WHEN accepted_count=item_count THEN NULL
    WHEN accepted_count>0 THEN 'wecom_dispatch_partial' ELSE 'wecom_dispatch_failed' END,
   updated_at=terminalized WHERE intent_id=d.intent_id AND state_version=d.state_version RETURNING * INTO d;
 END IF;
 INSERT INTO agent_runtime_scheduled_wecom_unsupported_requests(
  request_id,intent_id,item_id,claim_request_id,lease_token,worker_id,
  expected_delivery_state_version,expected_item_state_version,reason_code,result_item_status,
  result_delivery_status,result_delivery_state_version,result_item_state_version,terminalized_at)
 VALUES(p_request_id,d.intent_id,item.id,p_claim_request_id,p_lease_token,btrim(p_worker_id),
  p_expected_delivery_state_version,p_expected_item_state_version,reason,item.status,d.status,
  d.state_version,item.state_version,terminalized) RETURNING * INTO request;
 RETURN _agent_runtime_scheduled_wecom_unsupported_json(request,'terminalized');
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_UNSUPPORTED_REQUEST_CONFLICT' USING ERRCODE='55000';
END $$;

COMMENT ON FUNCTION terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1(
 UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT) IS
 'Durably cancels one server-verified unsupported unsent item and releases or aggregates its delivery without transport or retry.';

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_unsupported_immutable(),
 _agent_runtime_scheduled_wecom_unsupported_request_guard(),
 _agent_runtime_scheduled_wecom_unsupported_json(
  agent_runtime_scheduled_wecom_unsupported_requests,TEXT),
 terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1(
  UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1(
 UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT) TO everydayai_wecom_runtime;

RESET ROLE;
