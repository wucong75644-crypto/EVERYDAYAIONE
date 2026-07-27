SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_command_claims) THEN
        RAISE EXCEPTION 'AGENT_COMMAND_CLAIM_ROLLBACK_FACTS_PRESENT';
    END IF;
END;
$$;

DROP FUNCTION finish_agent_command_claim(UUID, UUID, TEXT, TEXT);
DROP FUNCTION renew_agent_command_claim(UUID, UUID, INTEGER);
DROP FUNCTION get_agent_command_run_claim(UUID, TEXT);
DROP FUNCTION claim_pending_agent_command_and_ensure_run(TEXT, INTEGER, INTEGER);
DROP FUNCTION _ensure_agent_command_run(
    agent_session_commands, agent_runtime_sessions,
    agent_command_claims, JSONB, UUID);
DROP FUNCTION _finish_exhausted_agent_command(
    agent_session_commands, agent_command_claims);
DROP FUNCTION _reject_agent_command(
    agent_session_commands, agent_runtime_sessions, TEXT, TEXT);
DROP FUNCTION _agent_run_request_hash(UUID, TEXT, JSONB, JSONB, JSONB);
DROP FUNCTION _agent_command_run_envelope(agent_session_commands);

RESET ROLE;
