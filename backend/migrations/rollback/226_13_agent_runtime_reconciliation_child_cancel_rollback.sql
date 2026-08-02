SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM agent_action_results WHERE action_id IN (SELECT id FROM agent_actions)) THEN
    RAISE EXCEPTION 'ROLLBACK_GUARD_FACTS_EXIST';
  END IF;
END $$;
REVOKE ALL ON FUNCTION record_agent_action_provider_still_accepted(UUID,UUID,BIGINT,TEXT,JSONB,TIMESTAMPTZ), record_agent_action_provider_still_unknown(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ), read_agent_child_run_strict_v2(UUID,UUID,UUID,UUID,TEXT,UUID,INTEGER,INTEGER) FROM everydayai_agent_runtime_worker;
DROP FUNCTION record_agent_action_provider_still_accepted(UUID,UUID,BIGINT,TEXT,JSONB,TIMESTAMPTZ);
DROP FUNCTION record_agent_action_provider_still_unknown(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ);
DROP FUNCTION read_agent_child_run_strict_v2(UUID,UUID,UUID,UUID,TEXT,UUID,INTEGER,INTEGER);
ALTER TABLE agent_runs DROP COLUMN child_terminal_result;
RESET ROLE;
