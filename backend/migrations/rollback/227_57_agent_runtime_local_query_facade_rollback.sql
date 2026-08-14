SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION execute_agent_runtime_local_query_v1(
 UUID,TEXT,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB
) FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai;
DROP FUNCTION execute_agent_runtime_local_query_v1(
 UUID,TEXT,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB
);

RESET ROLE;
