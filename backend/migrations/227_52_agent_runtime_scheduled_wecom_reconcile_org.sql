-- 227_52: Restore tenant identity on Scheduled Runtime WeCom reconcile claims.

SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE helper REGPROCEDURE;
BEGIN
 helper:=to_regprocedure(
  'public._agent_runtime_scheduled_wecom_reconcile_json('
  'agent_runtime_scheduled_wecom_reconcile_claim_requests,'
  'agent_runtime_scheduled_wecom_deliveries,'
  'agent_runtime_scheduled_wecom_delivery_items,'
  'agent_runtime_scheduled_wecom_dispatch_attempts,text)');
 IF helper IS NULL
 OR EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=helper
  AND(NOT p.prosecdef OR p.provolatile<>'s'
   OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, public']
   OR pg_get_userbyid(p.proowner)<>'everydayai_owner'))
 OR EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL
  aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl
  WHERE p.oid=helper AND acl.grantee=0 AND acl.privilege_type='EXECUTE')
 OR has_function_privilege('everydayai_wecom_runtime',helper,'EXECUTE') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_ORG_DEPENDENCY_DRIFT'
   USING ERRCODE='55000';
 END IF;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_json(
 p_request agent_runtime_scheduled_wecom_reconcile_claim_requests,
 p_delivery agent_runtime_scheduled_wecom_deliveries,
 p_item agent_runtime_scheduled_wecom_delivery_items,
 p_attempt agent_runtime_scheduled_wecom_dispatch_attempts,p_outcome TEXT) RETURNS JSONB
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('outcome',p_outcome,'request_id',p_request.request_id,
  'intent_id',p_request.intent_id,'org_id',p_delivery.org_id,
  'item_id',p_request.item_id,'attempt_id',p_request.attempt_id,
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
