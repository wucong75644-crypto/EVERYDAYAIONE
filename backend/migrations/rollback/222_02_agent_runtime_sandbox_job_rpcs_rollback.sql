SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_sandbox_jobs) THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_RPC_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END
$guard$;

DROP FUNCTION record_sandbox_job_cleanup(UUID,UUID,BIGINT,TEXT,JSONB);
DROP FUNCTION resolve_sandbox_job_reconciliation(
    UUID,UUID,BIGINT,TEXT,TEXT,TEXT,JSONB);
DROP FUNCTION renew_sandbox_job_reconciliation(UUID,UUID,BIGINT,INTEGER);
DROP FUNCTION claim_sandbox_job_reconciliation(UUID,BIGINT,TEXT,INTEGER);
DROP FUNCTION record_sandbox_job_unknown(
    UUID,UUID,BIGINT,BIGINT,JSONB,JSONB,TIMESTAMPTZ);
DROP FUNCTION finish_sandbox_job(UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TEXT,JSONB);
DROP FUNCTION record_sandbox_cancel_signal(UUID,UUID,BIGINT,BIGINT,TEXT);
DROP FUNCTION request_sandbox_job_cancel(UUID,BIGINT);
DROP FUNCTION recover_expired_sandbox_job(UUID,BIGINT);
DROP FUNCTION mark_sandbox_job_started(UUID,UUID,BIGINT,BIGINT,TEXT);
DROP FUNCTION renew_sandbox_job_lease(UUID,UUID,BIGINT,BIGINT,INTEGER);
DROP FUNCTION claim_next_sandbox_job(TEXT,INTEGER);
DROP FUNCTION get_sandbox_job(UUID);
DROP FUNCTION create_or_get_sandbox_job(
    UUID,UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,
    JSONB,JSONB);
DROP FUNCTION _lock_agent_sandbox_job(UUID);
DROP FUNCTION _agent_sandbox_runtime_job(agent_sandbox_jobs);
DROP FUNCTION _agent_sandbox_runtime_scope_ok(
    agent_runtime_sessions,agent_actions);
DROP FUNCTION _assert_agent_sandbox_actor(TEXT);

RESET ROLE;
