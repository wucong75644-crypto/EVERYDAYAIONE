SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_recovery_intents)
       OR EXISTS (SELECT 1 FROM agent_runtime_recovery_audit) THEN
        RAISE EXCEPTION 'AR_17_5_ROLLBACK_BLOCKED_RECOVERY_FACTS' USING ERRCODE='55000';
    END IF;
END $$;
DROP FUNCTION claim_agent_runtime_recovery(UUID,TEXT,INTEGER);
DROP FUNCTION request_agent_runtime_recovery(UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT);
DROP FUNCTION list_agent_runtime_recovery_snapshot(UUID,TEXT,TEXT,INTEGER);
DROP FUNCTION _agent_runtime_recovery_snapshot_row(TEXT,TEXT,UUID,TEXT,BIGINT,UUID,UUID,UUID,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,TEXT,TEXT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN,JSONB);
DROP TRIGGER agent_runtime_recovery_audit_immutable ON agent_runtime_recovery_audit;
DROP FUNCTION _agent_runtime_recovery_audit_immutable();
DROP TRIGGER agent_runtime_recovery_intent_immutable ON agent_runtime_recovery_intents;
DROP FUNCTION _agent_runtime_recovery_intent_immutable();
DROP TABLE agent_runtime_recovery_audit;
DROP TABLE agent_runtime_recovery_intents;
RESET ROLE;
