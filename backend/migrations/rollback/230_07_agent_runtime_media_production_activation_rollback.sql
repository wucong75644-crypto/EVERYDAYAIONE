SET LOCAL ROLE everydayai_owner;
DROP FUNCTION IF EXISTS set_agent_runtime_media_production_state_v1(UUID,UUID,UUID,BIGINT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN,TEXT);
DROP FUNCTION IF EXISTS get_agent_runtime_media_admin_context_v1();
RESET ROLE;
