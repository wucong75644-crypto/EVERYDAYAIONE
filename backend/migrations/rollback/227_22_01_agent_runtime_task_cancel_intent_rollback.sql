-- Roll back AR-18-A1.2-B1 facts only when no durable intent exists.
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_task_cancel_intents) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_ROLLBACK_FACTS_EXIST'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP FUNCTION request_agent_runtime_task_cancel_v1(
    UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT);
DROP FUNCTION _apply_agent_runtime_task_cancel_intent(
    agent_runtime_task_cancel_intents, agent_runtime_sessions,
    agent_session_commands, agent_runs);
DROP FUNCTION _lock_agent_runtime_task_cancel_intent(UUID);
DROP FUNCTION _agent_runtime_task_cancel_request_hash(
    UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT);
DROP TRIGGER guard_agent_runtime_task_cancel_intent_identity
    ON agent_runtime_task_cancel_intents;
DROP FUNCTION _guard_agent_runtime_task_cancel_intent_identity();
DROP TABLE agent_runtime_task_cancel_intents;

RESET ROLE;
