-- Roll back 227_52 by restoring the exact 227_41 reconcile helper output.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_json(
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

RESET ROLE;
