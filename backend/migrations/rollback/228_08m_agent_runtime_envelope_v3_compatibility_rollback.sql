SET LOCAL ROLE everydayai_owner;

-- Restore the exact v2-only predicates. The function bodies and privileges
-- otherwise remain identical to migration 228.08m.
DO $rollback$
DECLARE model_definition TEXT; read_definition TEXT;
BEGIN
 SELECT pg_get_functiondef('get_agent_runtime_model_context_v2(uuid,text,uuid)'::regprocedure)
  INTO model_definition;
 IF model_definition NOT LIKE '%schema_revision%NOT IN (%2%,%3%)%' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_CONTEXT_ROLLBACK_DRIFT';
 END IF;
 EXECUTE replace(
  model_definition,
  'c.payload->''run_envelope''->>''schema_revision'' IS NULL
    OR c.payload->''run_envelope''->>''schema_revision'' NOT IN (''2'',''3'')',
  'c.payload->''run_envelope''->>''schema_revision'' IS DISTINCT FROM ''2'''
 );

 SELECT pg_get_functiondef('_agent_runtime_read_context(uuid,uuid,uuid,text,text,integer)'::regprocedure)
  INTO read_definition;
 IF read_definition NOT LIKE '%schema_revision%NOT IN (%2%,%3%)%' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_READ_CONTEXT_ROLLBACK_DRIFT';
 END IF;
 EXECUTE replace(
  read_definition,
  'cmd.payload->''run_envelope''->>''schema_revision'' IS NULL
       OR cmd.payload->''run_envelope''->>''schema_revision'' NOT IN (''2'',''3'')',
  'cmd.payload->''run_envelope''->>''schema_revision'' IS DISTINCT FROM ''2'''
 );
END $rollback$;

RESET ROLE;
