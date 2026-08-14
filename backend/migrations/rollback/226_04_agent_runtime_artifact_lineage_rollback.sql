SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM agent_action_artifact_links) THEN RAISE EXCEPTION 'AGENT_RUNTIME_226_ROLLBACK_GUARD_FACTS_EXIST'; END IF; END $$;
DROP FUNCTION checkpoint_agent_artifact_materialization(UUID,UUID,UUID,INTEGER,TEXT); DROP FUNCTION link_agent_action_artifact(UUID,UUID,UUID,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT); DROP TABLE agent_action_artifact_links;
RESET ROLE;
