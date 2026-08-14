SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_sandbox_jobs) THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_RECOVERY_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION
    get_sandbox_job_by_binding(
        TEXT,UUID,UUID,UUID,TEXT,UUID,UUID,UUID,UUID,TEXT,INTEGER,TEXT
    ),
    claim_next_recoverable_sandbox_job(TEXT,INTEGER),
    claim_next_sandbox_job_reconciliation(TEXT,INTEGER),
    get_owned_sandbox_job(UUID,TEXT,UUID,BIGINT),
    record_reconciled_sandbox_partials(UUID,UUID,BIGINT,JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sandbox_worker, everydayai_sync, everydayai;

DROP FUNCTION claim_next_sandbox_job_reconciliation(TEXT,INTEGER);
DROP FUNCTION claim_next_recoverable_sandbox_job(TEXT,INTEGER);
DROP FUNCTION get_owned_sandbox_job(UUID,TEXT,UUID,BIGINT);
DROP FUNCTION record_reconciled_sandbox_partials(UUID,UUID,BIGINT,JSONB);
DROP FUNCTION get_sandbox_job_by_binding(
    TEXT,UUID,UUID,UUID,TEXT,UUID,UUID,UUID,UUID,TEXT,INTEGER,TEXT
);

GRANT EXECUTE ON FUNCTION get_sandbox_job(UUID)
TO everydayai_sandbox_worker;

RESET ROLE;
