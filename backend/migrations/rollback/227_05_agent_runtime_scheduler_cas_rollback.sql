SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_scheduler_cas_facts) THEN
        RAISE EXCEPTION 'AR174_A8_ROLLBACK_GUARD_FACTS_EXIST';
    END IF;
END $$;

REVOKE EXECUTE ON FUNCTION mutate_agent_runtime_scheduler_cas(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,JSONB,TEXT,UUID,TEXT),
    recover_agent_runtime_scheduler_cas(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT,UUID,TEXT)
    FROM everydayai_agent_runtime_worker;
DROP FUNCTION IF EXISTS recover_agent_runtime_scheduler_cas(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT,UUID,TEXT);
DROP FUNCTION IF EXISTS mutate_agent_runtime_scheduler_cas(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,JSONB,TEXT,UUID,TEXT);
DROP FUNCTION IF EXISTS _agent_runtime_scheduler_cas_payload_safe(JSONB);
DROP FUNCTION IF EXISTS _agent_runtime_scheduler_cas_context(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT);
DROP TABLE IF EXISTS agent_runtime_scheduler_cas_facts;

RESET ROLE;
