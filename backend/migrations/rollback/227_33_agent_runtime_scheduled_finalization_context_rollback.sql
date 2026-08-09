SET LOCAL ROLE everydayai_owner;
REVOKE ALL ON FUNCTION apply_agent_runtime_scheduled_finalization_v2(
 UUID,UUID,BIGINT,BIGINT,TEXT,UUID,TEXT,TIMESTAMPTZ)
 FROM everydayai_agent_runtime_worker;
DROP FUNCTION apply_agent_runtime_scheduled_finalization_v2(
 UUID,UUID,BIGINT,BIGINT,TEXT,UUID,TEXT,TIMESTAMPTZ);
REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID)
 FROM everydayai_agent_runtime_worker;
DROP FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID);
RESET ROLE;
