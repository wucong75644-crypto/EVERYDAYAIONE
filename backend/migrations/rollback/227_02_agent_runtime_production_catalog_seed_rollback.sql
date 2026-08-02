SET LOCAL ROLE everydayai_owner;
DO $$
DECLARE rev TEXT;
BEGIN
  SELECT catalog_revision INTO rev FROM agent_runtime_definition_facts
   WHERE agent_key='everydayai-default' AND definition_revision='v3';
  IF rev IS NULL THEN RETURN; END IF;
  IF EXISTS (SELECT 1 FROM agent_runtime_shadow_mismatches)
     OR EXISTS (SELECT 1 FROM agent_runtime_rollout_subjects)
     OR EXISTS (SELECT 1 FROM agent_runtime_definition_facts WHERE definition_revision='v3' AND used_by_ingress) THEN
    RAISE EXCEPTION 'AR174_02_ROLLBACK_GUARD';
  END IF;
  DELETE FROM agent_runtime_production_bindings WHERE catalog_revision=rev;
  DELETE FROM agent_runtime_effective_toolset_facts WHERE catalog_revision=rev;
  DELETE FROM agent_runtime_definition_facts WHERE agent_key='everydayai-default' AND definition_revision='v3';
  DELETE FROM agent_runtime_catalog_facts WHERE catalog_revision=rev;
END $$;
RESET ROLE;
