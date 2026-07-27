SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_command_claims) THEN
        RAISE EXCEPTION 'AGENT_COMMAND_CLAIM_ROLLBACK_FACTS_PRESENT';
    END IF;
END;
$$;

DROP TABLE agent_command_claims;

RESET ROLE;
