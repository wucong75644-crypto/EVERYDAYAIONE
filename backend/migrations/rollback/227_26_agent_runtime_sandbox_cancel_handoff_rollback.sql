-- Restore the pre-227_26 Sandbox cancel boundary only with no dependent facts.
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM agent_action_attempts attempt
        JOIN agent_actions action ON action.id=attempt.action_id
        LEFT JOIN agent_sandbox_jobs job ON job.attempt_id=attempt.id
        WHERE action.tool_name='code_execute' AND (
            attempt.reconciliation_operation='cancel'
            OR job.cancel_requested_at IS NOT NULL
            OR job.status='cancel_requested'
            OR (job.status='unknown' AND (
                job.cleanup_status IN ('pending','running','failed','unknown')
                OR job.reconciliation_token IS NOT NULL))
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_CANCEL_HANDOFF_ROLLBACK_PENDING_FACTS'
            USING ERRCODE='55000';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION
    request_agent_runtime_sandbox_cancel_v1(UUID,UUID,UUID,BIGINT,TEXT),
    claim_next_sandbox_cancel_v1(TEXT,INTEGER),
    finalize_agent_action_sandbox_cancel_v1(UUID,UUID,BIGINT,TEXT,UUID,BIGINT,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_agent_model_gateway,everydayai_projection_worker,
    everydayai_authorization_worker,everydayai_sandbox_worker;

DROP FUNCTION finalize_agent_action_sandbox_cancel_v1(
    UUID,UUID,BIGINT,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION request_agent_runtime_sandbox_cancel_v1(
    UUID,UUID,UUID,BIGINT,TEXT);
DROP FUNCTION claim_next_sandbox_cancel_v1(TEXT,INTEGER);
DROP TRIGGER agent_sandbox_cancel_terminal_fence ON agent_sandbox_jobs;
DROP FUNCTION _agent_sandbox_cancel_terminal_guard_v1();

GRANT EXECUTE ON FUNCTION request_sandbox_job_cancel(UUID,BIGINT)
TO everydayai_agent_runtime_worker;

RESET ROLE;
