SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_sandbox_jobs) THEN
        RAISE EXCEPTION 'AGENT_SANDBOX_JOB_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END
$guard$;

DROP TABLE agent_sandbox_jobs;
DROP FUNCTION _agent_sandbox_receipt_hash(JSONB);
DROP FUNCTION _agent_sandbox_receipt_is_valid(JSONB);
DROP FUNCTION _agent_sandbox_evidence_is_valid(JSONB);
DROP FUNCTION _agent_sandbox_manifest_is_valid(JSONB,TEXT);
DROP FUNCTION _agent_sandbox_summary_is_safe(TEXT);
DROP FUNCTION _agent_sandbox_json_is_safe(JSONB);

RESET ROLE;
