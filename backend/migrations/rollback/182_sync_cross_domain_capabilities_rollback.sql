SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS service_create_platform_alert(TEXT);
DROP FUNCTION IF EXISTS service_create_org_alert(UUID, TEXT);
DROP FUNCTION IF EXISTS sync_get_org_label(UUID);
DROP FUNCTION IF EXISTS sync_list_wecom_employees(UUID);
DROP FUNCTION IF EXISTS sync_list_erp_token_versions();
DROP FUNCTION IF EXISTS sync_discover_erp_targets();
DROP FUNCTION IF EXISTS sync_mark_oss_file_purged(BIGINT, TEXT);
DROP FUNCTION IF EXISTS sync_list_oss_purge_candidates(INTEGER);
DROP FUNCTION IF EXISTS sync_cleanup_error_logs(INTEGER);
DROP FUNCTION IF EXISTS worker_cleanup_error_logs(INTEGER);
DROP FUNCTION IF EXISTS sync_record_error_log(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
);
DROP FUNCTION IF EXISTS _record_service_error_log_core(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
);

RESET ROLE;
