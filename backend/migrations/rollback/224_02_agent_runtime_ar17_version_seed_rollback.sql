SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_definition_facts
      WHERE agent_key='everydayai-default' AND definition_revision IN ('v1','v2')
        AND used_by_ingress)
    OR EXISTS(SELECT 1 FROM agent_session_commands c
       WHERE c.payload->'run_envelope'->>'schema_revision'='2')
    OR EXISTS(SELECT 1 FROM agent_runs r
       JOIN agent_session_commands c ON c.id=r.command_id
       WHERE c.payload->'run_envelope'->>'schema_revision'='2') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_224_ROLLBACK_GUARD_FACTS_EXIST';
 END IF;
END $$;
DELETE FROM agent_runtime_effective_toolset_facts
 WHERE agent_key='everydayai-default' AND definition_revision IN ('v1','v2')
   AND catalog_revision IN (
    '9ef52c52816e357a4cb2bf03a9893e41127105a3ffb4c2cba18489fa880ce874',
    '563239a5d5d5d2dbc75600e65067a15f10d2a295adc47ab95742a49fc029781a');
DELETE FROM agent_runtime_definition_facts
 WHERE agent_key='everydayai-default' AND definition_revision IN ('v1','v2')
   AND catalog_revision IN (
    '9ef52c52816e357a4cb2bf03a9893e41127105a3ffb4c2cba18489fa880ce874',
    '563239a5d5d5d2dbc75600e65067a15f10d2a295adc47ab95742a49fc029781a');
DELETE FROM agent_runtime_catalog_facts c
 WHERE c.catalog_revision IN (
    '9ef52c52816e357a4cb2bf03a9893e41127105a3ffb4c2cba18489fa880ce874',
    '563239a5d5d5d2dbc75600e65067a15f10d2a295adc47ab95742a49fc029781a')
   AND NOT EXISTS (SELECT 1 FROM agent_runtime_definition_facts d
     WHERE d.catalog_revision=c.catalog_revision)
   AND NOT EXISTS (SELECT 1 FROM agent_runtime_effective_toolset_facts e
     WHERE e.catalog_revision=c.catalog_revision);
RESET ROLE;
