"""Static contracts for the Sync service database boundary."""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
SYNC_BOUNDARY = (MIGRATIONS / "181_sync_data_domain_boundary.sql").read_text()
CROSS_DOMAIN = (
    MIGRATIONS / "182_sync_cross_domain_capabilities.sql"
).read_text()
CONFIGURATION = (
    MIGRATIONS / "183_sync_configuration_capabilities.sql"
).read_text()
OPERATOR_CONTROL = (
    MIGRATIONS / "184_runtime_erp_operator_control.sql"
).read_text()
EXTERNAL_QUEUE = (
    MIGRATIONS / "185_external_sync_request_queue.sql"
).read_text()
WECOM_EMPLOYEE_ACCESS = (
    MIGRATIONS / "219_sync_wecom_employee_capability_access.sql"
).read_text()
WECOM_EMPLOYEE_ACCESS_ROLLBACK = (
    MIGRATIONS
    / "rollback/219_sync_wecom_employee_capability_access_rollback.sql"
).read_text()
DEPLOY = MIGRATIONS.parent.parent / "deploy"
WECOM_EMPLOYEE_ADMIN_GRANT = (
    DEPLOY / "grant-sync-wecom-employee-access.sh"
).read_text()
WECOM_EMPLOYEE_ADMIN_ROLLBACK = (
    DEPLOY / "rollback-sync-wecom-employee-access.sh"
).read_text()


def test_sync_domain_tables_force_row_security() -> None:
    assert "ENABLE ROW LEVEL SECURITY" in SYNC_BOUNDARY
    assert "FORCE ROW LEVEL SECURITY" in SYNC_BOUNDARY
    for table in (
        "erp_products",
        "erp_stock_status",
        "erp_sync_state",
        "kuaimai_external_credentials",
        "kuaimai_sync_logs",
    ):
        assert f"'{table}'" in SYNC_BOUNDARY


def test_legacy_credentials_are_not_granted_to_sync() -> None:
    sync_write_section = SYNC_BOUNDARY.split(
        "sync_write_tables CONSTANT TEXT[] := ARRAY[", 1
    )[1].split("];", 1)[0]
    assert "kuaimai_external_credentials" not in sync_write_section


def test_materialized_view_is_only_exposed_by_scoped_capabilities() -> None:
    assert "CREATE OR REPLACE FUNCTION sync_refresh_kit_stock()" in SYNC_BOUNDARY
    assert "CREATE OR REPLACE FUNCTION runtime_list_kit_stock()" in SYNC_BOUNDARY
    assert (
        "GRANT EXECUTE ON FUNCTION sync_refresh_kit_stock()\n"
        "TO everydayai_sync"
    ) in SYNC_BOUNDARY
    assert (
        "GRANT EXECUTE ON FUNCTION runtime_list_kit_stock()\n"
        "TO everydayai_runtime"
    ) in SYNC_BOUNDARY
    assert "REVOKE ALL ON TABLE public.mv_kit_stock" in SYNC_BOUNDARY


def test_cross_domain_access_uses_narrow_role_checked_facades() -> None:
    for function in (
        "sync_record_error_log",
        "sync_cleanup_error_logs",
        "sync_list_oss_purge_candidates",
        "sync_mark_oss_file_purged",
        "sync_discover_erp_targets",
        "sync_list_wecom_employees",
        "service_create_org_alert",
        "service_create_platform_alert",
    ):
        assert f"FUNCTION {function}" in CROSS_DOMAIN
    assert "session_user NOT IN ('everydayai_sync', 'everydayai_worker')" in (
        CROSS_DOMAIN
    )


def test_sync_configuration_supports_discovery_and_atomic_token_rotation() -> None:
    for function in (
        "sync_discover_external_targets",
        "sync_commit_erp_token_pair",
    ):
        assert f"FUNCTION {function}" in CONFIGURATION
    assert "p_expected_version" in CONFIGURATION
    assert "public._write_configuration_entry(" in CONFIGURATION


def test_runtime_operator_writes_use_admin_scoped_capabilities() -> None:
    assert "_assert_configuration_runtime_org_admin()" in OPERATOR_CONTROL
    assert "runtime_bind_erp_operator" in OPERATOR_CONTROL
    assert "runtime_unbind_erp_operator" in OPERATOR_CONTROL
    assert "TO everydayai_runtime" in OPERATOR_CONTROL


def test_external_manual_sync_uses_durable_fenced_queue() -> None:
    assert "CREATE TABLE kuaimai_external_sync_requests" in EXTERNAL_QUEUE
    assert "FOR UPDATE SKIP LOCKED" in EXTERNAL_QUEUE
    assert "execution_token" in EXTERNAL_QUEUE
    assert "lease_expires_at" in EXTERNAL_QUEUE
    assert "sync_renew_external_sync" in EXTERNAL_QUEUE
    assert "RETURNING * INTO v_request" in EXTERNAL_QUEUE
    assert "FORCE ROW LEVEL SECURITY" in EXTERNAL_QUEUE


def test_wecom_employee_facades_have_only_required_column_access() -> None:
    required_columns = "org_id, wecom_userid, name, status"
    assert f"GRANT SELECT ({required_columns})" in (
        WECOM_EMPLOYEE_ADMIN_GRANT
    )
    assert "ON TABLE public.wecom_employees" in WECOM_EMPLOYEE_ADMIN_GRANT
    assert "TO everydayai_owner" in WECOM_EMPLOYEE_ADMIN_GRANT
    assert "GRANT SELECT ON TABLE" not in WECOM_EMPLOYEE_ADMIN_GRANT
    assert f"REVOKE SELECT ({required_columns})" in (
        WECOM_EMPLOYEE_ADMIN_ROLLBACK
    )
    assert "FROM everydayai_owner" in WECOM_EMPLOYEE_ADMIN_ROLLBACK
    assert "has_column_privilege(" in WECOM_EMPLOYEE_ACCESS
    assert "has_any_column_privilege(" in WECOM_EMPLOYEE_ACCESS
    assert "SYNC_WECOM_EMPLOYEE_DIRECT_ACCESS_INVALID" in (
        WECOM_EMPLOYEE_ACCESS
    )
    assert "REVOKE SELECT" not in WECOM_EMPLOYEE_ACCESS_ROLLBACK
