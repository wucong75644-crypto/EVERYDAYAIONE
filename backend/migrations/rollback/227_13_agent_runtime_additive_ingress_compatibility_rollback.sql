SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_sessions)
       OR EXISTS (SELECT 1 FROM agent_session_commands) THEN
        RAISE EXCEPTION 'AR_17_4_ROLLBACK_BLOCKED_INGRESS_FACTS' USING ERRCODE='55000';
    END IF;
END $$;
REVOKE EXECUTE ON FUNCTION get_agent_runtime_ingress_capability(),
 runtime_submit_ingress_v5(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB)
 FROM everydayai_runtime,everydayai_wecom_runtime;
DROP FUNCTION runtime_submit_ingress_v5(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB);
DROP FUNCTION get_agent_runtime_ingress_capability();
DROP FUNCTION _agent_runtime_ingress_kill_epoch_context(UUID);
RESET ROLE;
