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
DROP FUNCTION get_agent_runtime_model_context_v2(UUID,TEXT,UUID);
DROP FUNCTION set_agent_runtime_definition_ingress_enabled(TEXT,TEXT,BOOLEAN);
DROP FUNCTION get_agent_runtime_version_facts(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT);
DROP FUNCTION get_agent_runtime_definition_fact(TEXT,TEXT);
DROP FUNCTION ensure_agent_runtime_definition_fact(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT);
DROP FUNCTION enqueue_wecom_runtime_turn_v4(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT);
DROP FUNCTION runtime_submit_ingress_v2(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,
 TEXT,TEXT,JSONB,JSONB,TEXT,JSONB);
DROP TABLE agent_runtime_effective_toolset_facts;
DROP TABLE agent_runtime_catalog_facts;
DROP TABLE agent_runtime_definition_facts;
RESET ROLE;
