SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_finalization_intents
  WHERE application_request_id IS NOT NULL OR status='applied') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_APPLICATION_FACTS_EXIST' USING ERRCODE='55000';
 END IF;
END $$;
REVOKE ALL ON FUNCTION apply_agent_runtime_scheduled_finalization_v1(
 UUID,UUID,BIGINT,BIGINT,TEXT,UUID,TEXT,TIMESTAMPTZ) FROM everydayai_agent_runtime_worker;
DROP FUNCTION apply_agent_runtime_scheduled_finalization_v1(UUID,UUID,BIGINT,BIGINT,TEXT,UUID,TEXT,TIMESTAMPTZ);
DROP FUNCTION _agent_runtime_scheduled_safe_summary(TEXT);
DROP FUNCTION _agent_runtime_scheduled_application_hash(UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TIMESTAMPTZ);
DROP TRIGGER runtime_scheduled_finalization_application_guard ON agent_runtime_scheduled_finalization_intents;
DROP FUNCTION _agent_runtime_scheduled_application_guard();
DROP INDEX uq_runtime_scheduled_finalization_application_request;
ALTER TABLE agent_runtime_scheduled_finalization_intents
 DROP CONSTRAINT runtime_scheduled_finalization_application_shape,
 DROP COLUMN application_request_id,DROP COLUMN application_hash,
 DROP COLUMN application_receipt,DROP COLUMN applied_at;
RESET ROLE;
