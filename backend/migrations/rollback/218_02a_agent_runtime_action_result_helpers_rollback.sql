SET LOCAL ROLE everydayai_owner;
DROP FUNCTION _apply_agent_tool_terminal(
    agent_model_attempts,agent_model_steps,agent_runs,UUID,JSONB,TEXT,TEXT,
    JSONB,TEXT,JSONB,JSONB,JSONB);
DROP FUNCTION _insert_agent_action_batch(agent_model_steps,JSONB,JSONB,TEXT);
DROP FUNCTION _agent_action_result_hash(JSONB, TEXT, UUID, UUID);
RESET ROLE;
