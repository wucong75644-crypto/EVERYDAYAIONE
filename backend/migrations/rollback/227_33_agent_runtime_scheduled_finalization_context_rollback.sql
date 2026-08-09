SET LOCAL ROLE everydayai_owner;
REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID)
 FROM everydayai_agent_runtime_worker;
DROP FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID);
RESET ROLE;
