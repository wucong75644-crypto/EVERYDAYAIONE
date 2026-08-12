-- 227_60 rollback: only remove adoption facts/contracts; never touch scheduled_tasks.
SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_scheduled_adoption_profiles)
       OR EXISTS (SELECT 1 FROM agent_runtime_scheduled_adoption_provenance) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_ADOPTION_FACTS_EXIST';
    END IF;
END $$;
DROP FUNCTION IF EXISTS rollback_agent_runtime_scheduled_adoption_v1(UUID);
DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_adoption_v1(UUID);
DROP FUNCTION IF EXISTS adopt_agent_runtime_scheduled_tasks_v1(JSONB,UUID);
DROP TRIGGER IF EXISTS scheduled_adoption_profile_immutable ON agent_runtime_scheduled_adoption_profiles;
DROP TRIGGER IF EXISTS scheduled_adoption_provenance_immutable ON agent_runtime_scheduled_adoption_provenance;
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_adoption_immutable();
DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_profiles;
DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_provenance;
RESET ROLE;
