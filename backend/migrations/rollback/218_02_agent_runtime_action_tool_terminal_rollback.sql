SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION complete_model_attempt_step_and_create_actions(
    UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT,
    JSONB, INTEGER, TEXT, JSONB
) FROM everydayai_worker;
DROP FUNCTION complete_model_attempt_step_and_create_actions(
    UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT,
    JSONB, INTEGER, TEXT, JSONB
);
DROP FUNCTION _agent_action_batch_hash(JSONB);
DROP FUNCTION _canonical_agent_action_batch(agent_model_steps, JSONB);

RESET ROLE;
