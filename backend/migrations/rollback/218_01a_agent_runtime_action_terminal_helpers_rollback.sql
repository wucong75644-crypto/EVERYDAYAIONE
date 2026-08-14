SET LOCAL ROLE everydayai_owner;

DROP FUNCTION _validate_agent_action_batch(JSONB, JSONB);
DROP FUNCTION _replay_agent_action_batch(
    agent_model_attempts, agent_model_steps, agent_runs,
    TEXT, JSONB, TEXT, TEXT, JSONB, INTEGER, TEXT, JSONB);

RESET ROLE;
