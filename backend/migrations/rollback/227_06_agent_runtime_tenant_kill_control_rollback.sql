SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_tenant_gate_controls)
       OR EXISTS (SELECT 1 FROM agent_runtime_owner_fences)
       OR EXISTS (SELECT 1 FROM agent_runtime_kill_audit) THEN
        RAISE EXCEPTION 'AR173_A_ROLLBACK_GUARD_FACTS_EXIST';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION set_agent_runtime_tenant_gate(
    UUID,UUID,TEXT,TEXT,BOOLEAN,BIGINT,TEXT)
    FROM everydayai_runtime_admin;
REVOKE ALL ON FUNCTION get_agent_runtime_tenant_gate_status(UUID)
    FROM everydayai_runtime_admin;
REVOKE ALL ON FUNCTION get_agent_runtime_owner_fence(TEXT,UUID,UUID)
    FROM everydayai_agent_runtime_worker, everydayai_projection_worker,
         everydayai_authorization_worker, everydayai_sandbox_worker;
DROP FUNCTION get_agent_runtime_owner_fence(TEXT,UUID,UUID);
DROP FUNCTION get_agent_runtime_tenant_gate_status(UUID);
DROP FUNCTION set_agent_runtime_tenant_gate(UUID,UUID,TEXT,TEXT,BOOLEAN,BIGINT,TEXT);
DROP TRIGGER agent_runtime_kill_audit_immutable ON agent_runtime_kill_audit;
DROP FUNCTION _agent_runtime_kill_audit_immutable();
DROP TABLE agent_runtime_kill_audit;
DROP TABLE agent_runtime_owner_fences;
DROP TABLE agent_runtime_tenant_gate_controls;

RESET ROLE;
