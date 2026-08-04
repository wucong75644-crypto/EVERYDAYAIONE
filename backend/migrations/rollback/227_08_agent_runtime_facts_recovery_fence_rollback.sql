SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_owner_fences WHERE status='active') THEN
        RAISE EXCEPTION 'AR_17_3_C_ROLLBACK_BLOCKED_ACTIVE_OWNER_FENCE' USING ERRCODE='55000';
    END IF;
END $$;
DROP TRIGGER agent_runtime_provider_facts_epoch_fence ON agent_runtime_provider_submission_facts;
DROP TRIGGER agent_runtime_scheduler_facts_epoch_fence ON agent_runtime_scheduler_cas_facts;
DROP TRIGGER agent_runtime_sandbox_epoch_fence ON agent_sandbox_jobs;
DROP TRIGGER IF EXISTS agent_runtime_child_run_epoch_fence ON agent_runs;
DROP FUNCTION _agent_runtime_provider_facts_epoch_trigger();
DROP FUNCTION _agent_runtime_scheduler_facts_epoch_trigger();
DROP FUNCTION _agent_runtime_sandbox_epoch_trigger();
DROP FUNCTION _agent_runtime_child_run_epoch_trigger();
DROP FUNCTION _agent_runtime_assert_facts_epoch(UUID,UUID,UUID,TEXT,TEXT,TEXT);
RESET ROLE;
