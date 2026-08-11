SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION get_agent_runtime_resource_manifest_v1(
 UUID,TEXT,UUID,BIGINT,TEXT
) FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai;
DROP FUNCTION get_agent_runtime_resource_manifest_v1(
 UUID,TEXT,UUID,BIGINT,TEXT
);

RESET ROLE;
