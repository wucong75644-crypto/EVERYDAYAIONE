-- 227_48: Durable recovery of stale dispatch_started Scheduled Runtime WeCom attempts.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_wecom_started_recovery_requests(
 request_id UUID PRIMARY KEY,recovery_worker_id TEXT NOT NULL CHECK(length(recovery_worker_id) BETWEEN 1 AND 128),
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 intent_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_id UUID NOT NULL REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 attempt_id UUID NOT NULL UNIQUE REFERENCES agent_runtime_scheduled_wecom_dispatch_attempts(id) ON DELETE RESTRICT,
 claim_request_id UUID NOT NULL,lease_token UUID NOT NULL,
 claim_worker_id TEXT NOT NULL CHECK(length(claim_worker_id) BETWEEN 1 AND 128),
 provider_request_id TEXT NOT NULL CHECK(length(provider_request_id) BETWEEN 8 AND 200),
 idempotency_key TEXT NOT NULL CHECK(idempotency_key~'^[0-9a-f]{64}$'),
 provider_revision BIGINT NOT NULL CHECK(provider_revision>0),
 outcome_request_id UUID NOT NULL UNIQUE
  REFERENCES agent_runtime_scheduled_wecom_outcome_requests(request_id) ON DELETE RESTRICT,
 original_delivery_state_version BIGINT NOT NULL CHECK(original_delivery_state_version>=1),
 original_item_state_version BIGINT NOT NULL CHECK(original_item_state_version>=1),
 result_attempt_status TEXT NOT NULL CHECK(result_attempt_status='unknown'),
 result_dispatch_phase TEXT NOT NULL CHECK(result_dispatch_phase='ambiguous'),
 result_item_status TEXT NOT NULL CHECK(result_item_status='unknown'),
 result_delivery_status TEXT NOT NULL CHECK(result_delivery_status='unknown'),
 result_delivery_state_version BIGINT NOT NULL CHECK(result_delivery_state_version>=1),
 result_item_state_version BIGINT NOT NULL CHECK(result_item_state_version>=1),
 recovered_at TIMESTAMPTZ NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK(request_id<>outcome_request_id),UNIQUE(request_id,org_id,intent_id,item_id,attempt_id)
);
ALTER TABLE agent_runtime_scheduled_wecom_started_recovery_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_started_recovery_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_started_recovery_owner
 ON agent_runtime_scheduled_wecom_started_recovery_requests
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_started_recovery_requests
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_started_recovery_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_IMMUTABLE' USING ERRCODE='55000';
END $$;
CREATE TRIGGER runtime_scheduled_wecom_started_recovery_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_started_recovery_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_started_recovery_immutable();

CREATE FUNCTION _agent_runtime_scheduled_wecom_started_recovery_request_guard() RETURNS TRIGGER
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
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_wecom_started_recovery_global_request_guard BEFORE INSERT
 ON agent_runtime_scheduled_wecom_started_recovery_requests FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_wecom_started_recovery_request_guard();

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
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_started_recovery_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF; RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items item
  WHERE(item.id,item.intent_id)=(NEW.item_id,NEW.intent_id)) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_IDENTITY_INVALID' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries d WHERE d.claim_request_id=NEW.request_id
   AND(d.intent_id,d.claim_worker_id,d.lease_token,d.lease_expires_at,d.state_version)
    IS DISTINCT FROM(NEW.intent_id,NEW.worker_id,NEW.lease_token,NEW.lease_expires_at,NEW.delivery_state_version))
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_started_recovery_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF; RETURN NEW;
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
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_started_recovery_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF; RETURN NEW;
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
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_started_recovery_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_DEFINITIVE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF; RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_unsupported_request_guard() RETURNS TRIGGER
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
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_started_recovery_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_UNSUPPORTED_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF; RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_legacy_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE guard_request_id UUID;
BEGIN
 IF TG_TABLE_NAME='agent_runtime_scheduled_wecom_deliveries' THEN
  IF NEW.claim_request_id IS NULL OR NEW.claim_request_id IS NOT DISTINCT FROM OLD.claim_request_id THEN RETURN NEW; END IF;
  guard_request_id:=NEW.claim_request_id;
 ELSE guard_request_id:=NEW.request_id; END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(guard_request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_started_recovery_requests WHERE request_id=guard_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF; RETURN NEW;
END $$;

CREATE FUNCTION _agent_runtime_scheduled_wecom_started_recovery_json(
 p_request agent_runtime_scheduled_wecom_started_recovery_requests,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'request_id',p_request.request_id,
  'recovery_worker_id',p_request.recovery_worker_id,'org_id',p_request.org_id,
  'intent_id',p_request.intent_id,'item_id',p_request.item_id,'attempt_id',p_request.attempt_id,
  'outcome_request_id',p_request.outcome_request_id,'dispatch_outcome','unknown',
  'attempt_status',p_request.result_attempt_status,'dispatch_phase',p_request.result_dispatch_phase,
  'item_status',p_request.result_item_status,'delivery_status',p_request.result_delivery_status,
  'delivery_state_version',p_request.result_delivery_state_version,
  'item_state_version',p_request.result_item_state_version,'recovered_at',p_request.recovered_at)
$$;

CREATE FUNCTION recover_agent_runtime_scheduled_wecom_started_dispatch_v1(
 p_request_id UUID,p_recovery_worker_id TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d agent_runtime_scheduled_wecom_deliveries%ROWTYPE;
 item agent_runtime_scheduled_wecom_delivery_items%ROWTYPE;
 a agent_runtime_scheduled_wecom_dispatch_attempts%ROWTYPE;
 request agent_runtime_scheduled_wecom_started_recovery_requests%ROWTYPE;
 candidate_intent UUID;candidate_item UUID;candidate_attempt UUID;outcome_request UUID;
 original_delivery_version BIGINT;original_item_version BIGINT;result JSONB;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 IF p_request_id IS NULL OR length(btrim(COALESCE(p_recovery_worker_id,''))) NOT BETWEEN 1 AND 128 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(p_request_id);
 SELECT * INTO request FROM agent_runtime_scheduled_wecom_started_recovery_requests WHERE request_id=p_request_id;
 IF FOUND THEN
  IF request.recovery_worker_id IS DISTINCT FROM btrim(p_recovery_worker_id) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_REQUEST_CONFLICT' USING ERRCODE='55000'; END IF;
  RETURN _agent_runtime_scheduled_wecom_started_recovery_json(request,'readback');
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=p_request_id OR reconcile_request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=p_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=p_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_REQUEST_CONFLICT' USING ERRCODE='55000'; END IF;
 SELECT cd.intent_id,ci.id,ca.id INTO candidate_intent,candidate_item,candidate_attempt
 FROM agent_runtime_scheduled_wecom_deliveries cd
 JOIN agent_runtime_scheduled_wecom_delivery_items ci ON ci.intent_id=cd.intent_id
 JOIN agent_runtime_scheduled_wecom_dispatch_attempts ca ON ca.item_id=ci.id
 WHERE cd.status='dispatching' AND cd.lease_expires_at<=clock_timestamp()
  AND cd.claim_request_id IS NOT NULL AND cd.lease_token IS NOT NULL AND cd.claim_worker_id IS NOT NULL
  AND cd.reconcile_request_id IS NULL AND cd.reconcile_token IS NULL
  AND cd.reconcile_worker_id IS NULL AND cd.reconcile_lease_expires_at IS NULL
  AND ci.status='dispatching' AND ca.status='dispatch_started'
  AND ca.dispatch_phase='external_request_started' AND ca.dispatch_started_at IS NOT NULL
  AND ca.receipt_type IS NULL AND ca.receipt_hash IS NULL AND ca.receipt_code IS NULL
  AND ca.unknown_at IS NULL AND ca.resolved_at IS NULL AND NOT ca.was_ambiguous
  AND(ca.claim_request_id,ca.lease_token,ca.claim_worker_id)
   IS NOT DISTINCT FROM(cd.claim_request_id,cd.lease_token,cd.claim_worker_id)
  AND ca.provider_revision=cd.provider_revision
  AND cd.state_version=ca.prepared_delivery_state_version+2
  AND ci.state_version=ca.prepared_item_state_version+2
 ORDER BY ca.dispatch_started_at,ca.id FOR UPDATE OF cd SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','empty'); END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=candidate_intent;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=candidate_item FOR UPDATE;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=candidate_attempt FOR UPDATE;
 IF(d.org_id,d.intent_id,d.status,d.claim_request_id,d.lease_token,d.claim_worker_id,d.provider_revision,
    item.intent_id,item.status,a.item_id,a.status,a.dispatch_phase,a.claim_request_id,a.lease_token,
    a.claim_worker_id,a.provider_revision)
  IS DISTINCT FROM(d.org_id,candidate_intent,'dispatching',a.claim_request_id,a.lease_token,a.claim_worker_id,
   a.provider_revision,d.intent_id,'dispatching',item.id,'dispatch_started','external_request_started',
   d.claim_request_id,d.lease_token,d.claim_worker_id,d.provider_revision)
 OR d.lease_expires_at>clock_timestamp() OR d.reconcile_token IS NOT NULL
 OR a.dispatch_started_at IS NULL OR a.receipt_type IS NOT NULL OR a.receipt_hash IS NOT NULL
 OR a.receipt_code IS NOT NULL OR a.unknown_at IS NOT NULL OR a.resolved_at IS NOT NULL OR a.was_ambiguous THEN
  RETURN jsonb_build_object('outcome','empty'); END IF;
 original_delivery_version:=d.state_version;original_item_version:=item.state_version;
 outcome_request:=gen_random_uuid();
 IF outcome_request=p_request_id THEN outcome_request:=gen_random_uuid(); END IF;
 result:=record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(outcome_request,d.intent_id,item.id,a.id,
  d.claim_request_id,d.lease_token,d.claim_worker_id,d.state_version,item.state_version,
  a.provider_request_id,a.idempotency_key,a.provider_revision,'unknown',NULL,NULL,NULL,'{}'::JSONB);
 IF result->>'outcome' NOT IN('recorded','readback') OR result->>'request_id' IS DISTINCT FROM outcome_request::TEXT
 OR result->>'intent_id' IS DISTINCT FROM d.intent_id::TEXT OR result->>'item_id' IS DISTINCT FROM item.id::TEXT
 OR result->>'attempt_id' IS DISTINCT FROM a.id::TEXT OR result->>'dispatch_outcome'<>'unknown'
 OR result->>'attempt_status'<>'unknown' OR result->>'item_status'<>'unknown'
 OR result->>'delivery_status'<>'unknown' OR result->'receipt_metadata'<>'{}'::JSONB
 OR result->>'receipt_type' IS NOT NULL OR result->>'receipt_hash' IS NOT NULL OR result->>'receipt_code' IS NOT NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_OUTCOME_INVALID' USING ERRCODE='55000'; END IF;
 SELECT * INTO d FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=candidate_intent;
 SELECT * INTO item FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=candidate_item;
 SELECT * INTO a FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=candidate_attempt;
 IF(d.status,item.status,a.status,a.dispatch_phase) IS DISTINCT FROM('unknown','unknown','unknown','ambiguous')
 OR d.state_version IS DISTINCT FROM(result->>'delivery_state_version')::BIGINT
 OR item.state_version IS DISTINCT FROM(result->>'item_state_version')::BIGINT OR a.unknown_at IS NULL
 OR d.claim_request_id IS NOT NULL OR d.lease_token IS NOT NULL OR d.claim_worker_id IS NOT NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_STATE_INVALID' USING ERRCODE='55000'; END IF;
 INSERT INTO agent_runtime_scheduled_wecom_started_recovery_requests(
  request_id,recovery_worker_id,org_id,intent_id,item_id,attempt_id,claim_request_id,lease_token,
  claim_worker_id,provider_request_id,idempotency_key,provider_revision,outcome_request_id,
  original_delivery_state_version,original_item_state_version,result_attempt_status,result_dispatch_phase,
  result_item_status,result_delivery_status,result_delivery_state_version,result_item_state_version,recovered_at)
 VALUES(p_request_id,btrim(p_recovery_worker_id),d.org_id,d.intent_id,item.id,a.id,a.claim_request_id,
  a.lease_token,a.claim_worker_id,a.provider_request_id,a.idempotency_key,a.provider_revision,outcome_request,
  original_delivery_version,original_item_version,a.status,a.dispatch_phase,item.status,d.status,
  d.state_version,item.state_version,a.unknown_at) RETURNING * INTO request;
 RETURN _agent_runtime_scheduled_wecom_started_recovery_json(request,'recovered');
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_REQUEST_CONFLICT' USING ERRCODE='55000';
END $$;

COMMENT ON FUNCTION recover_agent_runtime_scheduled_wecom_started_dispatch_v1(UUID,TEXT) IS
 'Converts one expired dispatch_started attempt to durable UNKNOWN through the authoritative outcome RPC; never resubmits.';
REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_started_recovery_immutable(),
 _agent_runtime_scheduled_wecom_started_recovery_request_guard(),
 _agent_runtime_scheduled_wecom_started_recovery_json(agent_runtime_scheduled_wecom_started_recovery_requests,TEXT),
 recover_agent_runtime_scheduled_wecom_started_dispatch_v1(UUID,TEXT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION recover_agent_runtime_scheduled_wecom_started_dispatch_v1(UUID,TEXT)
 TO everydayai_wecom_runtime;

RESET ROLE;
