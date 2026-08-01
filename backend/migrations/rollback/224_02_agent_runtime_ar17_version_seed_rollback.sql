SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_definition_facts WHERE used_by_ingress)
    OR EXISTS(SELECT 1 FROM agent_session_commands c
       WHERE c.payload->'run_envelope'->>'schema_revision'='2')
    OR EXISTS(SELECT 1 FROM agent_runs r
       JOIN agent_session_commands c ON c.id=r.command_id
       WHERE c.payload->'run_envelope'->>'schema_revision'='2') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_224_ROLLBACK_GUARD_FACTS_EXIST';
 END IF;
END $$;
DELETE FROM agent_runtime_effective_toolset_facts;
DELETE FROM agent_runtime_definition_facts;
DELETE FROM agent_runtime_catalog_facts;
RESET ROLE;
