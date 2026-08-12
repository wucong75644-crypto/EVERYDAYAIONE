-- 227_60 rollback: only remove adoption facts/contracts; never touch scheduled_tasks.
SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_scheduled_adoption_profiles)
       OR EXISTS (SELECT 1 FROM agent_runtime_scheduled_adoption_provenance) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_ADOPTION_FACTS_EXIST';
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_runtime_scheduled_execution_profiles
        WHERE source_action_id IS NULL
          AND source_attempt_id IS NULL
          AND source_run_id IS NULL
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_ADOPTION_EXECUTION_FACTS_EXIST';
    END IF;
END $$;
DROP FUNCTION IF EXISTS rollback_agent_runtime_scheduled_adoption_v1(UUID);
DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_adoption_v1(UUID);
DROP FUNCTION IF EXISTS adopt_agent_runtime_scheduled_tasks_v1(JSONB,UUID);
DROP TRIGGER IF EXISTS scheduled_adoption_profile_immutable ON agent_runtime_scheduled_adoption_profiles;
DROP TRIGGER IF EXISTS scheduled_adoption_provenance_immutable ON agent_runtime_scheduled_adoption_provenance;
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_adoption_immutable();
DROP TRIGGER IF EXISTS runtime_scheduled_profile_immutable ON agent_runtime_scheduled_execution_profiles;
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_profile_immutable();
CREATE TRIGGER runtime_scheduled_profile_immutable
    BEFORE UPDATE OR DELETE ON agent_runtime_scheduled_execution_profiles
    FOR EACH ROW EXECUTE FUNCTION _runtime_scheduler_immutable_fact();
ALTER TABLE agent_runtime_scheduled_execution_profiles
    DROP CONSTRAINT IF EXISTS runtime_scheduled_profile_source_shape_check;
ALTER TABLE agent_runtime_scheduled_execution_profiles
    ALTER COLUMN source_action_id SET NOT NULL,
    ALTER COLUMN source_attempt_id SET NOT NULL,
    ALTER COLUMN source_run_id SET NOT NULL;
DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_profiles;
DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_provenance;
RESET ROLE;
