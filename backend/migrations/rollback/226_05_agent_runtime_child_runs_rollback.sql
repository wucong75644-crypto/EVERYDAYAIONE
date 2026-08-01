SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM agent_runs WHERE parent_run_id IS NOT NULL) THEN RAISE EXCEPTION 'AGENT_RUNTIME_226_ROLLBACK_GUARD_FACTS_EXIST'; END IF; END $$;
DROP FUNCTION cancel_agent_child_run(UUID,UUID,TEXT); DROP FUNCTION complete_agent_child_run(UUID,UUID,INTEGER,JSONB); DROP FUNCTION create_agent_child_run(UUID,UUID,TEXT,INTEGER,TEXT,JSONB); DROP INDEX uq_agent_child_ordinal,idx_agent_child_parent;
ALTER TABLE agent_runs DROP CONSTRAINT agent_run_child_pair, DROP COLUMN aggregation_revision, DROP COLUMN parent_request_hash, DROP COLUMN child_ordinal, DROP COLUMN parent_action_id, DROP COLUMN parent_run_id;
RESET ROLE;
