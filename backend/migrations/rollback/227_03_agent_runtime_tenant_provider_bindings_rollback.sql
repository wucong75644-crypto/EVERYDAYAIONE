SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM agent_runtime_tenant_provider_bindings) THEN
   RAISE EXCEPTION 'AR174_03_ROLLBACK_GUARD';
 END IF;
END $$;
REVOKE ALL ON FUNCTION resolve_agent_runtime_tenant_provider_binding(TEXT,TEXT,TEXT,TEXT,UUID)
 FROM everydayai_agent_runtime_worker, everydayai_worker;
DROP FUNCTION IF EXISTS resolve_agent_runtime_tenant_provider_binding(TEXT,TEXT,TEXT,TEXT,UUID);
DROP TABLE IF EXISTS agent_runtime_tenant_provider_bindings;
RESET ROLE;
