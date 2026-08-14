SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_runs WHERE parent_run_id IS NOT NULL)
       OR EXISTS (SELECT 1 FROM agent_actions WHERE status IN ('completed','failed','cancelled')) THEN
        RAISE EXCEPTION 'ROLLBACK_GUARD_FACTS_EXIST';
    END IF;
END $$;
REVOKE ALL ON FUNCTION cancel_agent_child_run_strict(UUID,UUID,UUID,TEXT,TEXT),complete_agent_child_run_strict(UUID,UUID,UUID,TEXT,INTEGER,JSONB),read_agent_child_run_strict(UUID,UUID,UUID,TEXT),create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB) FROM everydayai_agent_runtime_worker;
DROP FUNCTION cancel_agent_child_run_strict(UUID,UUID,UUID,TEXT,TEXT);
DROP FUNCTION complete_agent_child_run_strict(UUID,UUID,UUID,TEXT,INTEGER,JSONB);
DROP FUNCTION read_agent_child_run_strict(UUID,UUID,UUID,TEXT);
DROP FUNCTION create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB);
RESET ROLE;
