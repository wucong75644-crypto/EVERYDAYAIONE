SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
 get_agent_runtime_erp_configuration_v1(UUID,TEXT,UUID,BIGINT,TEXT),
 rotate_agent_runtime_erp_token_pair_v1(
  UUID,TEXT,UUID,BIGINT,TEXT,JSONB,BIGINT
 )
FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai;
DROP FUNCTION rotate_agent_runtime_erp_token_pair_v1(
 UUID,TEXT,UUID,BIGINT,TEXT,JSONB,BIGINT
);
DROP FUNCTION get_agent_runtime_erp_configuration_v1(
 UUID,TEXT,UUID,BIGINT,TEXT
);
DROP FUNCTION _agent_runtime_erp_read_context_v1(
 UUID,TEXT,UUID,BIGINT,TEXT,TEXT
);
DROP FUNCTION _agent_runtime_erp_read_fence_v1(UUID,UUID,UUID);

RESET ROLE;
