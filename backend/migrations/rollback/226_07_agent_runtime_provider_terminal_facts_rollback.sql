SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_action_attempts WHERE status IN ('completed','failed','cancelled','unknown'))
       OR EXISTS (SELECT 1 FROM agent_actions WHERE status IN ('completed','failed','cancelled','unknown')) THEN
        RAISE EXCEPTION 'ROLLBACK_GUARD_FACTS_EXIST';
    END IF;
END $$;
REVOKE ALL ON FUNCTION record_agent_action_provider_terminal(UUID,UUID,TEXT,TEXT,JSONB,JSONB) FROM everydayai_agent_runtime_worker;
DROP FUNCTION record_agent_action_provider_terminal(UUID,UUID,TEXT,TEXT,JSONB,JSONB);
RESET ROLE;
