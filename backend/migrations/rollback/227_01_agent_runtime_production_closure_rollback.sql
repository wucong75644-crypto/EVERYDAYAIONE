SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM agent_runtime_shadow_mismatches)
    OR EXISTS (SELECT 1 FROM agent_runtime_production_bindings)
    OR EXISTS (SELECT 1 FROM agent_runtime_rollout_subjects) THEN
   RAISE EXCEPTION 'AR227_ROLLBACK_HAS_PRODUCTION_FACTS';
 END IF;
END $$;
DROP FUNCTION IF EXISTS runtime_submit_ingress_v3(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB);
DROP FUNCTION IF EXISTS enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT);
DROP FUNCTION IF EXISTS record_agent_runtime_shadow_mismatch(UUID,UUID,TEXT,TEXT,TEXT,JSONB);
DROP FUNCTION IF EXISTS set_agent_runtime_rollout_subject(TEXT,TEXT,TEXT,BOOLEAN,JSONB);
DROP TABLE IF EXISTS agent_runtime_shadow_mismatches;
DROP TABLE IF EXISTS agent_runtime_production_bindings;
DROP TABLE IF EXISTS agent_runtime_rollout_subjects;
RESET ROLE;
