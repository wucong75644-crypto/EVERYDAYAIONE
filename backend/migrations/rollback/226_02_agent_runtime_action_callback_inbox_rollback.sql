SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM agent_action_callback_inbox) THEN RAISE EXCEPTION 'AGENT_RUNTIME_226_ROLLBACK_GUARD_FACTS_EXIST'; END IF; END $$;
REVOKE ALL ON FUNCTION record_agent_action_callback(TEXT,TEXT,TEXT,TEXT,JSONB,BOOLEAN,UUID,UUID) FROM everydayai_agent_runtime_worker;
DROP FUNCTION record_agent_action_callback(TEXT,TEXT,TEXT,TEXT,JSONB,BOOLEAN,UUID,UUID); DROP TABLE agent_action_callback_inbox;
RESET ROLE;
