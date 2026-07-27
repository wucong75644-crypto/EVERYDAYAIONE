SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    claim_ready_agent_actions(TEXT, TEXT, INTEGER, INTEGER),
    get_agent_action_claim_batch(TEXT, TEXT),
    renew_agent_action_attempt(UUID, UUID, BIGINT, INTEGER),
    mark_agent_action_dispatching(UUID, UUID, BIGINT, TEXT),
    recover_expired_agent_action_attempt(UUID, BIGINT, TEXT, INTEGER),
    fail_claimed_agent_action(UUID, UUID, BIGINT, TEXT, TEXT),
    complete_agent_action(UUID, UUID, BIGINT, TEXT, JSONB),
    fail_agent_action(UUID, UUID, BIGINT, TEXT, JSONB)
FROM everydayai_worker;
DROP FUNCTION fail_agent_action(UUID, UUID, BIGINT, TEXT, JSONB);
DROP FUNCTION complete_agent_action(UUID, UUID, BIGINT, TEXT, JSONB);
DROP FUNCTION fail_claimed_agent_action(UUID, UUID, BIGINT, TEXT, TEXT);
DROP FUNCTION _finish_agent_action(UUID, UUID, BIGINT, TEXT, TEXT, JSONB);
DROP FUNCTION recover_expired_agent_action_attempt(UUID, BIGINT, TEXT, INTEGER);
DROP FUNCTION mark_agent_action_dispatching(UUID, UUID, BIGINT, TEXT);
DROP FUNCTION renew_agent_action_attempt(UUID, UUID, BIGINT, INTEGER);
DROP FUNCTION get_agent_action_claim_batch(TEXT, TEXT);
DROP FUNCTION claim_ready_agent_actions(TEXT, TEXT, INTEGER, INTEGER);

RESET ROLE;
