SET LOCAL ROLE everydayai_owner;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_web_projection_receipts)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_web_wakeup_attempts) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_ROLLBACK_HAS_FACTS'
   USING ERRCODE='55000';
 END IF;
END $$;

DROP FUNCTION complete_agent_runtime_scheduled_web_wakeup_v1(UUID,UUID,BIGINT,BOOLEAN,TEXT);
DROP FUNCTION get_agent_runtime_scheduled_web_projection_v1(UUID);
DROP FUNCTION read_agent_runtime_scheduled_web_projection_claim_v1(UUID);
DROP FUNCTION apply_agent_runtime_scheduled_web_projection_v1(UUID,UUID,BIGINT);
DROP FUNCTION claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER);
DROP FUNCTION _agent_runtime_scheduled_web_projection_payload(
 agent_runtime_scheduled_web_projection_receipts);
DROP FUNCTION _agent_runtime_scheduled_web_projection_facts(UUID);
DROP TABLE agent_runtime_scheduled_web_wakeup_attempts;
DROP TABLE agent_runtime_scheduled_web_projection_receipts;

RESET ROLE;
