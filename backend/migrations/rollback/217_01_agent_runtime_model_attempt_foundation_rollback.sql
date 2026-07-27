SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_model_attempts) THEN
        RAISE EXCEPTION 'AGENT_MODEL_ATTEMPT_ROLLBACK_FACTS_PRESENT:attempts'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TABLE IF EXISTS agent_model_attempts;

RESET ROLE;
