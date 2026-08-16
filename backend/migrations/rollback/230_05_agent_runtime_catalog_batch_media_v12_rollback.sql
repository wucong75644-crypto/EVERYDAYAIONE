SET LOCAL ROLE everydayai_owner;
DO $$
DECLARE rev TEXT;
BEGIN
  SELECT catalog_revision INTO rev FROM agent_runtime_definition_facts
   WHERE agent_key='everydayai-default' AND definition_revision='v12';
  IF rev IS NULL THEN RETURN; END IF;
  IF EXISTS (SELECT 1 FROM agent_runtime_definition_facts
       WHERE agent_key='everydayai-default' AND definition_revision='v12'
         AND used_by_ingress)
     OR EXISTS (SELECT 1 FROM agent_runs
       WHERE config_snapshot->>'tool_catalog_revision'=rev
          OR capability_snapshot->>'effective_toolset_revision'=rev)
     OR EXISTS (SELECT 1 FROM agent_actions
       WHERE policy_snapshot->>'catalog_revision'=rev)
     OR EXISTS (SELECT 1 FROM agent_runtime_tenant_provider_bindings
       WHERE catalog_revision=rev) THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_CATALOG_BATCH_MEDIA_V12_ROLLBACK_GUARD';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM agent_runtime_definition_facts
       WHERE catalog_revision=rev AND definition_revision<>'v12') THEN
    DELETE FROM agent_runtime_production_bindings WHERE catalog_revision=rev;
  END IF;
  DELETE FROM agent_runtime_effective_toolset_facts
   WHERE catalog_revision=rev AND definition_revision='v12';
  DELETE FROM agent_runtime_definition_facts
   WHERE agent_key='everydayai-default' AND definition_revision='v12';
  IF NOT EXISTS (SELECT 1 FROM agent_runtime_definition_facts
       WHERE catalog_revision=rev) THEN
    DELETE FROM agent_runtime_catalog_facts WHERE catalog_revision=rev;
  END IF;
END $$;
RESET ROLE;
