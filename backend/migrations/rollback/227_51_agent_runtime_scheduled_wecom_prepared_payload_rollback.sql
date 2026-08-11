SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_recovery_json(
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

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_recovery_json(
 agent_runtime_scheduled_wecom_prepared_recovery_requests,
 agent_runtime_scheduled_wecom_dispatch_attempts,TEXT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_wecom_prepared_payload_v1(
 UUID,UUID,UUID,UUID,INTEGER,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
DROP FUNCTION read_agent_runtime_scheduled_wecom_prepared_payload_v1(
 UUID,UUID,UUID,UUID,INTEGER,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT);
DROP FUNCTION _agent_runtime_scheduled_wecom_safe_payload_v2(
 JSONB,agent_runtime_scheduled_wecom_delivery_items,BIGINT,BIGINT);

RESET ROLE;
