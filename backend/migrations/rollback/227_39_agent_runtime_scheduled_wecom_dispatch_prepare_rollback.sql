-- Roll back 227_39 only while no A2b1 dispatch attempt fact exists.
SET LOCAL ROLE everydayai_owner;
LOCK TABLE agent_runtime_scheduled_wecom_dispatch_attempts IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items WHERE status='dispatching')
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries WHERE status='dispatching') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_DISPATCH_ROLLBACK_HAS_FACTS'
   USING ERRCODE='55000';
 END IF;
END $$;
REVOKE ALL ON FUNCTION
 prepare_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v1(UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 FROM everydayai_wecom_runtime;
DROP FUNCTION read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(
 UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT);
DROP FUNCTION start_agent_runtime_scheduled_wecom_dispatch_v1(
 UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT);
DROP FUNCTION prepare_agent_runtime_scheduled_wecom_dispatch_v1(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT);
DROP FUNCTION _agent_runtime_scheduled_wecom_attempt_matches(
 agent_runtime_scheduled_wecom_dispatch_attempts,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT);
DROP FUNCTION _agent_runtime_scheduled_wecom_attempt_json(
 agent_runtime_scheduled_wecom_dispatch_attempts,TEXT);
ALTER TABLE agent_runtime_scheduled_wecom_dispatch_attempts
 DROP COLUMN prepared_item_state_version,
 DROP COLUMN prepared_delivery_state_version,
 DROP COLUMN claim_worker_id,
 DROP COLUMN lease_token,
 DROP COLUMN claim_request_id;
RESET ROLE;
