SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION get_erp_runtime_bundle(),
    get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle()
FROM everydayai_sync;
DROP FUNCTION IF EXISTS runtime_delete_external_configuration(
    UUID, TEXT, BIGINT, BIGINT
);
DROP FUNCTION IF EXISTS runtime_set_external_configuration(
    UUID, TEXT, JSONB, TEXT, BIGINT, BIGINT
);
DROP FUNCTION IF EXISTS sync_commit_erp_token_pair(UUID, JSONB, BIGINT);
DROP FUNCTION IF EXISTS sync_discover_external_targets();
DROP FUNCTION IF EXISTS _assert_configuration_sync_org();

RESET ROLE;
