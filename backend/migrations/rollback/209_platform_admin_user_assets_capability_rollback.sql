SET LOCAL ROLE everydayai_owner;

DROP FUNCTION resolve_platform_admin_user_assets_download(UUID, JSONB);
DROP FUNCTION list_platform_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
);
ALTER FUNCTION _list_admin_user_assets_owner(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) RENAME TO list_admin_user_assets;
ALTER FUNCTION list_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) SET search_path = public;

REVOKE ALL ON FUNCTION list_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai, service_role;
GRANT EXECUTE ON FUNCTION list_admin_user_assets(
    UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
) TO service_role;

REVOKE ALL ON TABLE user_assets, user_asset_refs
FROM everydayai_runtime;

RESET ROLE;
