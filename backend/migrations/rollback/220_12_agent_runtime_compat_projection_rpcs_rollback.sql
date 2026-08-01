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

DROP FUNCTION get_agent_compat_projection_result(UUID);
DROP FUNCTION apply_agent_compat_projection(UUID, UUID, TEXT);
DROP FUNCTION claim_agent_compat_projection_outbox(INTEGER, INTEGER);
DROP FUNCTION _agent_compat_project_run(agent_runtime_events, TEXT);
DROP FUNCTION _agent_compat_project_completed_run(
    agent_runs, agent_runtime_sessions, agent_session_commands, tasks);
DROP FUNCTION _agent_compat_project_command(agent_runtime_events);
DROP FUNCTION _agent_compat_projection_action(agent_runtime_events);

RESET ROLE;
