SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_action_cost_settlements)
       OR EXISTS (SELECT 1 FROM agent_action_callback_inbox) THEN
        RAISE EXCEPTION 'ROLLBACK_GUARD_FACTS_EXIST';
    END IF;
END $$;
REVOKE ALL ON FUNCTION record_agent_action_callback_strict(TEXT,TEXT,TEXT,TEXT,JSONB,UUID,UUID),record_agent_action_cost_strict(UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) FROM everydayai_agent_runtime_worker;
DROP FUNCTION record_agent_action_callback_strict(TEXT,TEXT,TEXT,TEXT,JSONB,UUID,UUID);
DROP FUNCTION record_agent_action_cost_strict(UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT);
RESET ROLE;
