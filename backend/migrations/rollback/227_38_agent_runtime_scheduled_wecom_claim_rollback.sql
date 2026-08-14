-- Roll back 227_38 only while all 227_37 WeCom delivery facts remain pristine.
SET LOCAL ROLE everydayai_owner;
LOCK TABLE agent_runtime_scheduled_wecom_delivery_items,
 agent_runtime_scheduled_wecom_deliveries IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries d
  WHERE d.status<>'pending' OR d.state_version<>0 OR d.claim_worker_id IS NOT NULL
   OR d.claim_request_id IS NOT NULL OR d.lease_token IS NOT NULL OR d.lease_expires_at IS NOT NULL
   OR d.next_attempt_at IS NOT NULL OR d.terminal_reason_code IS NOT NULL)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items item
  WHERE item.status<>'pending' OR item.state_version<>0 OR item.next_attempt_at IS NOT NULL
   OR item.terminal_reason_code IS NOT NULL) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CLAIM_ROLLBACK_HAS_STATE'
   USING ERRCODE='55000';
 END IF;
END $$;
DROP FUNCTION read_agent_runtime_scheduled_wecom_dispatch_context_v1(UUID,UUID,UUID,TEXT,BIGINT);
DROP FUNCTION read_agent_runtime_scheduled_wecom_claim_v1(UUID);
DROP FUNCTION renew_agent_runtime_scheduled_wecom_delivery_lease_v1(UUID,UUID,UUID,TEXT,BIGINT,INTEGER);
DROP FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v1(UUID,TEXT,INTEGER);
DROP FUNCTION _agent_runtime_scheduled_wecom_claim_json(agent_runtime_scheduled_wecom_deliveries,TEXT);
DROP FUNCTION _agent_runtime_scheduled_wecom_cancel_unavailable(UUID,TEXT);
DROP FUNCTION _agent_runtime_scheduled_wecom_live_context(UUID);
DROP FUNCTION _assert_agent_runtime_scheduled_wecom_actor();
RESET ROLE;
