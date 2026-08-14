SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_provider_operation_intents) THEN
        RAISE EXCEPTION 'AR_17_4_ROLLBACK_BLOCKED_OPERATION_INTENTS' USING ERRCODE='55000';
    END IF;
END $$;
DROP FUNCTION claim_agent_runtime_provider_operation(UUID,TEXT,INTEGER);
DROP FUNCTION request_agent_runtime_provider_operation(UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT);
DROP FUNCTION list_agent_runtime_provider_operations(UUID,TEXT,TEXT,TEXT,TIMESTAMPTZ,TIMESTAMPTZ,INTEGER);
DROP FUNCTION _agent_runtime_admin_org_check(UUID);
DROP TRIGGER agent_runtime_provider_operation_immutable ON agent_runtime_provider_operation_intents;
DROP FUNCTION _agent_runtime_provider_operation_immutable();
DROP TRIGGER agent_runtime_provider_fact_observation_stamp ON agent_runtime_provider_submission_facts;
DROP FUNCTION _agent_runtime_provider_fact_observation_stamp();
DROP TABLE agent_runtime_provider_operation_intents;
ALTER TABLE agent_runtime_provider_submission_facts
    DROP COLUMN last_readback_at,
    DROP COLUMN last_reconcile_at;
RESET ROLE;
