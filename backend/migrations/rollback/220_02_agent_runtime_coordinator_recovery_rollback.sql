SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS
    renew_model_attempt_execution(UUID, UUID, UUID, BIGINT, INTEGER),
    get_agent_run_aggregate(UUID, TEXT, UUID),
    claim_next_agent_run(TEXT, INTEGER, INTEGER),
    get_claimed_agent_run(TEXT);

RESET ROLE;
