SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE v_catalog_revision TEXT;
BEGIN
  SELECT catalog_revision INTO v_catalog_revision
    FROM agent_runtime_definition_facts
   WHERE agent_key='everydayai-default' AND definition_revision='v5';
  IF v_catalog_revision IS NULL THEN RETURN; END IF;
  IF EXISTS (
      SELECT 1 FROM agent_runtime_definition_facts
       WHERE agent_key='everydayai-default' AND definition_revision='v5'
         AND used_by_ingress
  ) OR EXISTS (
      SELECT 1 FROM agent_runs
       WHERE config_snapshot->>'tool_catalog_revision'=v_catalog_revision
          OR capability_snapshot->>'effective_toolset_revision'=v_catalog_revision
  ) OR EXISTS (
      SELECT 1 FROM agent_actions
       WHERE policy_snapshot->>'catalog_revision'=v_catalog_revision
  ) THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_ERP_READ_RELEASE_FACTS_EXIST';
  END IF;
  DELETE FROM agent_runtime_effective_toolset_facts
   WHERE catalog_revision=v_catalog_revision;
  DELETE FROM agent_runtime_definition_facts
   WHERE agent_key='everydayai-default' AND definition_revision='v5';
  DELETE FROM agent_runtime_catalog_facts
   WHERE catalog_revision=v_catalog_revision;
END $$;

RESET ROLE;
