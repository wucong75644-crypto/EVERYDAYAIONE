SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_action_sync_phase_facts) THEN RAISE EXCEPTION 'ROLLBACK_GUARD_FACTS_EXIST'; END IF;
END $$;
REVOKE ALL ON FUNCTION record_agent_sync_phase(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB) FROM everydayai_agent_runtime_worker;
DROP FUNCTION record_agent_sync_phase(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB);
DROP TABLE agent_action_sync_phase_facts;
RESET ROLE;
