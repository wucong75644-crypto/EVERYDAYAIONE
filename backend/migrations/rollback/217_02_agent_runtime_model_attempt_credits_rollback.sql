SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_model_credit_settlements) THEN
        RAISE EXCEPTION 'AGENT_MODEL_ATTEMPT_ROLLBACK_FACTS_PRESENT:settlements'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS
    _cancel_agent_model_work(UUID),
    _release_agent_model_credits(UUID),
    _fail_model_attempt_and_step(
        UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT
    ),
    _complete_model_attempt_without_actions(
        UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT,
        TEXT, TEXT, JSONB, INTEGER
    ),
    _settle_agent_model_credits(agent_model_steps, UUID, TEXT, INTEGER),
    _reserve_agent_model_credits(agent_model_steps, UUID, INTEGER);
DROP TABLE IF EXISTS agent_model_credit_settlements;

RESET ROLE;
