SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_compat_projection_results)
       OR EXISTS (
           SELECT 1 FROM agent_compat_projection_checkpoints
            WHERE through_sequence > 0
       ) THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END
$guard$;

DROP INDEX uq_tasks_agent_runtime_projection;
DROP INDEX uq_messages_agent_runtime_projection;
DROP TABLE agent_compat_projection_results;
DROP TABLE agent_compat_projection_checkpoints;

RESET ROLE;
