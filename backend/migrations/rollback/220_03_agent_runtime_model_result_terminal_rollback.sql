SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS
    get_agent_model_result(UUID),
    complete_model_attempt_with_result(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, JSONB,
        INTEGER, TEXT, TEXT, JSONB, TEXT, TEXT);

RESET ROLE;
