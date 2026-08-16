SET LOCAL ROLE everydayai_owner;
DO $$
DECLARE rev TEXT;
BEGIN
  SELECT catalog_revision INTO rev FROM agent_runtime_definition_facts
   WHERE agent_key='everydayai-default' AND definition_revision='v10';
  IF rev IS NULL THEN RETURN; END IF;
  IF EXISTS (SELECT 1 FROM agent_runtime_definition_facts
       WHERE agent_key='everydayai-default' AND definition_revision='v10'
         AND used_by_ingress)
     OR EXISTS (SELECT 1 FROM agent_runs
       WHERE config_snapshot->>'tool_catalog_revision'=rev
          OR capability_snapshot->>'effective_toolset_revision'=rev)
     OR EXISTS (SELECT 1 FROM agent_actions
       WHERE policy_snapshot->>'catalog_revision'=rev) THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_CATALOG_ERP_READ_V10_ROLLBACK_GUARD';
  END IF;
  DELETE FROM agent_runtime_effective_toolset_facts WHERE catalog_revision=rev;
  DELETE FROM agent_runtime_definition_facts
   WHERE agent_key='everydayai-default' AND definition_revision='v10';
  DELETE FROM agent_runtime_catalog_facts WHERE catalog_revision=rev;
END $$;
RESET ROLE;
