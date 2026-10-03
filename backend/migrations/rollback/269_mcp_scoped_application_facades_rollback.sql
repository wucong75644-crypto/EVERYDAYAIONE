SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION api_set_org_mcp_connector_credential(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
), api_delete_org_mcp_connector_credential(UUID, TEXT, BIGINT),
    api_get_org_mcp_connector_credential_status(UUID),
    api_record_org_mcp_connector_health(UUID, TEXT, TEXT, TEXT),
    api_set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN),
    api_get_org_mcp_connector_state(UUID, TEXT),
    api_get_mcp_test_readonly_bundle()
FROM everydayai, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, PUBLIC;

DROP FUNCTION IF EXISTS api_set_org_mcp_connector_credential(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
);
DROP FUNCTION IF EXISTS api_delete_org_mcp_connector_credential(
    UUID, TEXT, BIGINT
);
DROP FUNCTION IF EXISTS api_get_org_mcp_connector_credential_status(UUID);
DROP FUNCTION IF EXISTS api_record_org_mcp_connector_health(
    UUID, TEXT, TEXT, TEXT
);
DROP FUNCTION IF EXISTS api_set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN);
DROP FUNCTION IF EXISTS api_get_org_mcp_connector_state(UUID, TEXT);
DROP FUNCTION IF EXISTS api_get_mcp_test_readonly_bundle();
DROP FUNCTION IF EXISTS _assert_mcp_application_actor(UUID, BOOLEAN);

RESET ROLE;
