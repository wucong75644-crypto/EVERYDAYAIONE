SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION record_org_mcp_connector_health(UUID, TEXT, TEXT, TEXT),
    set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN),
    get_org_mcp_connector_state(UUID, TEXT),
    get_mcp_test_readonly_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
DROP FUNCTION IF EXISTS record_org_mcp_connector_health(UUID, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN);
DROP FUNCTION IF EXISTS get_org_mcp_connector_state(UUID, TEXT);
DROP FUNCTION IF EXISTS get_mcp_test_readonly_bundle();
DROP TABLE IF EXISTS organization_mcp_connectors;
UPDATE secret_records
   SET status = 'revoked', updated_at = NOW()
 WHERE secret_name = 'mcp.test_readonly_bearer_token';
DELETE FROM configuration_entries
 WHERE definition_version = 'v1'
   AND config_key = 'mcp.test_readonly.bearer_token';
DELETE FROM secret_records
 WHERE secret_name = 'mcp.test_readonly_bearer_token';
DELETE FROM configuration_bundle_definitions
 WHERE definition_version = 'v1' AND bundle_name = 'mcp.test_readonly';
DELETE FROM configuration_definitions
 WHERE definition_version = 'v1' AND config_key = 'mcp.test_readonly.bearer_token';

RESET ROLE;
