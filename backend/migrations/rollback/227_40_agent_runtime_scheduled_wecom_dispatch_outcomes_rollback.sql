-- Roll back 227_40 only while no dispatch outcome fact exists.
SET LOCAL ROLE everydayai_owner;
LOCK TABLE agent_runtime_scheduled_wecom_outcome_requests,
 agent_runtime_scheduled_wecom_dispatch_attempts IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
  WHERE status IN('accepted','rejected','unknown')) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_OUTCOME_ROLLBACK_HAS_FACTS'
   USING ERRCODE='55000';
 END IF;
END $$;
REVOKE ALL ON FUNCTION record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB)
 FROM everydayai_wecom_runtime;
DROP FUNCTION record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB);
DROP FUNCTION _agent_runtime_scheduled_wecom_outcome_json(
 agent_runtime_scheduled_wecom_outcome_requests,TEXT);
DROP TRIGGER runtime_scheduled_wecom_outcome_request_immutable
 ON agent_runtime_scheduled_wecom_outcome_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_outcome_request_immutable();
DROP TABLE agent_runtime_scheduled_wecom_outcome_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_receipt_hash(
 TEXT,TEXT,TEXT,JSONB,TEXT,TEXT,BIGINT);
DROP FUNCTION _agent_runtime_scheduled_wecom_receipt_metadata_valid(JSONB);
RESET ROLE;
