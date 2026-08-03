SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_provider_submission_facts) THEN
        RAISE EXCEPTION 'AR174_A2_ROLLBACK_GUARD_FACTS_EXIST';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION read_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT),
 reconcile_agent_runtime_provider_submission(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,JSONB),
 record_agent_runtime_provider_readback(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB),
 request_agent_runtime_provider_cancel(UUID,UUID,TEXT,BIGINT,TEXT),
 record_agent_runtime_provider_unknown(UUID,UUID,TEXT,BIGINT,JSONB),
 record_agent_runtime_provider_submitted(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT),
 create_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT),
 _agent_runtime_provider_evidence_safe(JSONB),
 _agent_runtime_provider_submission_context(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT)
 FROM everydayai_agent_runtime_worker, everydayai_worker, PUBLIC;
DROP FUNCTION read_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT);
DROP FUNCTION reconcile_agent_runtime_provider_submission(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,JSONB);
DROP FUNCTION record_agent_runtime_provider_readback(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB);
DROP FUNCTION request_agent_runtime_provider_cancel(UUID,UUID,TEXT,BIGINT,TEXT);
DROP FUNCTION record_agent_runtime_provider_unknown(UUID,UUID,TEXT,BIGINT,JSONB);
DROP FUNCTION record_agent_runtime_provider_submitted(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT);
DROP FUNCTION create_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT);
DROP FUNCTION _agent_runtime_provider_evidence_safe(JSONB);
DROP FUNCTION _agent_runtime_provider_submission_context(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT);
DROP INDEX idx_agent_runtime_provider_facts_binding;
DROP INDEX idx_agent_runtime_provider_facts_reconcile;
DROP TABLE agent_runtime_provider_submission_facts;
RESET ROLE;
