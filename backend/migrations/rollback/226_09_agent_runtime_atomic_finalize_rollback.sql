SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_action_results)
       OR EXISTS (SELECT 1 FROM agent_action_cost_settlements WHERE kind IN ('settle','release','refund','adjustment')) THEN
        RAISE EXCEPTION 'ROLLBACK_GUARD_FACTS_EXIST';
    END IF;
END $$;
REVOKE ALL ON FUNCTION finalize_agent_action_provider(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) FROM everydayai_agent_runtime_worker;
DROP FUNCTION finalize_agent_action_provider(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT);
RESET ROLE;
