-- 227_45: Authoritative Scheduled Runtime WeCom dispatch version readback.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_scheduled_wecom_dispatch_versioned_json(
 p_result JSONB,p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,
 p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT CASE
  WHEN p_result->>'outcome' NOT IN('prepared','dispatch_started','readback') THEN p_result
  ELSE COALESCE((
   SELECT p_result||jsonb_build_object(
    'delivery_state_version',delivery.state_version,
    'item_state_version',item.state_version)
   FROM agent_runtime_scheduled_wecom_deliveries delivery
   JOIN agent_runtime_scheduled_wecom_delivery_items item
    ON item.intent_id=delivery.intent_id
   JOIN agent_runtime_scheduled_wecom_dispatch_attempts attempt
    ON attempt.item_id=item.id
   WHERE delivery.intent_id=p_intent_id
    AND item.id=p_item_id
    AND attempt.id=p_attempt_id
    AND attempt.id::TEXT=p_result->>'attempt_id'
    AND item.id::TEXT=p_result->>'item_id'
    AND attempt.provider_request_id=btrim(p_provider_request_id)
    AND attempt.provider_request_id=p_result->>'provider_request_id'
    AND attempt.idempotency_key=p_idempotency_key
    AND attempt.idempotency_key=p_result->>'idempotency_key'
    AND attempt.provider_revision=p_provider_revision
    AND attempt.provider_revision::TEXT=p_result->>'provider_revision'
    AND attempt.status=p_result->>'status'
    AND(delivery.claim_request_id,delivery.lease_token,delivery.claim_worker_id)
     IS NOT DISTINCT FROM(p_claim_request_id,p_lease_token,btrim(p_worker_id))
    AND delivery.status IN('claimed','dispatching')
  ),jsonb_build_object('outcome','fenced'))
 END
$$;

CREATE FUNCTION prepare_agent_runtime_scheduled_wecom_dispatch_v2(
 p_intent_id UUID,p_item_id UUID,p_claim_request_id UUID,p_lease_token UUID,p_worker_id TEXT,
 p_expected_delivery_state_version BIGINT,p_expected_item_state_version BIGINT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE result JSONB;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 result:=prepare_agent_runtime_scheduled_wecom_dispatch_v1(
  p_intent_id,p_item_id,p_claim_request_id,p_lease_token,p_worker_id,
  p_expected_delivery_state_version,p_expected_item_state_version,
  p_provider_request_id,p_idempotency_key,p_provider_revision);
 IF result->>'outcome' NOT IN('prepared','readback') THEN RETURN result; END IF;
 RETURN _agent_runtime_scheduled_wecom_dispatch_versioned_json(
  result,p_intent_id,p_item_id,(result->>'attempt_id')::UUID,
  p_claim_request_id,p_lease_token,p_worker_id,p_provider_request_id,
  p_idempotency_key,p_provider_revision);
END $$;

CREATE FUNCTION start_agent_runtime_scheduled_wecom_dispatch_v2(
 p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,p_claim_request_id UUID,p_lease_token UUID,
 p_worker_id TEXT,p_expected_delivery_state_version BIGINT,p_expected_item_state_version BIGINT,
 p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE result JSONB;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 result:=start_agent_runtime_scheduled_wecom_dispatch_v1(
  p_intent_id,p_item_id,p_attempt_id,p_claim_request_id,p_lease_token,p_worker_id,
  p_expected_delivery_state_version,p_expected_item_state_version,
  p_provider_request_id,p_idempotency_key,p_provider_revision);
 IF result->>'outcome' NOT IN('dispatch_started','readback') THEN RETURN result; END IF;
 RETURN _agent_runtime_scheduled_wecom_dispatch_versioned_json(
  result,p_intent_id,p_item_id,p_attempt_id,p_claim_request_id,p_lease_token,p_worker_id,
  p_provider_request_id,p_idempotency_key,p_provider_revision);
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_wecom_dispatch_attempt_v2(
 p_intent_id UUID,p_item_id UUID,p_attempt_id UUID,p_claim_request_id UUID,p_lease_token UUID,
 p_worker_id TEXT,p_provider_request_id TEXT,p_idempotency_key TEXT,p_provider_revision BIGINT)
RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE result JSONB;
BEGIN
 PERFORM _assert_agent_runtime_scheduled_wecom_actor();
 result:=read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(
  p_intent_id,p_item_id,p_attempt_id,p_claim_request_id,p_lease_token,p_worker_id,
  p_provider_request_id,p_idempotency_key,p_provider_revision);
 IF result->>'outcome'<>'readback' THEN RETURN result; END IF;
 RETURN _agent_runtime_scheduled_wecom_dispatch_versioned_json(
  result,p_intent_id,p_item_id,p_attempt_id,p_claim_request_id,p_lease_token,p_worker_id,
  p_provider_request_id,p_idempotency_key,p_provider_revision);
END $$;

COMMENT ON FUNCTION prepare_agent_runtime_scheduled_wecom_dispatch_v2(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT) IS
 'Prepares or replays one stable attempt and returns its current authoritative delivery/item versions.';
COMMENT ON FUNCTION start_agent_runtime_scheduled_wecom_dispatch_v2(
 UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT) IS
 'Starts or replays one identity-bound attempt and returns versions directly usable by outcome recording.';
COMMENT ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_attempt_v2(
 UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT) IS
 'Pure identity-bound attempt readback with current authoritative delivery/item versions.';

REVOKE ALL ON FUNCTION
 _agent_runtime_scheduled_wecom_dispatch_versioned_json(
  JSONB,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT),
 prepare_agent_runtime_scheduled_wecom_dispatch_v2(
  UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v2(
  UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v2(
  UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

REVOKE ALL ON FUNCTION
 prepare_agent_runtime_scheduled_wecom_dispatch_v1(
  UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v1(
  UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(
  UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 FROM everydayai_wecom_runtime;

GRANT EXECUTE ON FUNCTION
 prepare_agent_runtime_scheduled_wecom_dispatch_v2(
  UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v2(
  UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v2(
  UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 TO everydayai_wecom_runtime;

RESET ROLE;
