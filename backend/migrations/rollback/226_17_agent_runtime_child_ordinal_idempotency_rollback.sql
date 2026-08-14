SET LOCAL ROLE everydayai_owner;
REVOKE ALL ON FUNCTION create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB) FROM everydayai_agent_runtime_worker;
DROP INDEX uq_agent_child_parent_ordinal;
RESET ROLE;
