from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND_DIR / "migrations" / "199_platform_error_monitor_capabilities.sql"
)
ROLLBACK = (
    BACKEND_DIR / "migrations" / "rollback"
    / "199_platform_error_monitor_capabilities_rollback.sql"
)


def test_error_monitor_uses_narrow_runtime_capabilities() -> None:
    sql = MIGRATION.read_text()
    expected = (
        "list_platform_error_logs",
        "get_platform_error_stats",
        "list_platform_error_summary",
        "resolve_platform_error",
        "clear_platform_errors",
    )
    for function in expected:
        assert f"FUNCTION {function}" in sql
    assert "PLATFORM_ADMIN_REQUIRED" in sql
    assert "TO everydayai_runtime" in sql
    assert "REVOKE ALL ON error_logs" in sql
    assert "DROP POLICY IF EXISTS platform_admin_error_logs_select" in sql


def test_error_and_permission_audits_are_forced_and_owner_accessible() -> None:
    sql = MIGRATION.read_text()
    assert "ALTER TABLE error_logs FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY error_logs_owner_all" in sql
    assert "ALTER TABLE permission_audit_log FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY permission_audit_log_owner_all" in sql
    assert "REVOKE ALL ON permission_audit_log" in sql


def test_rollback_restores_previous_error_log_access() -> None:
    sql = ROLLBACK.read_text()
    assert "GRANT SELECT, UPDATE, DELETE ON error_logs" in sql
    assert "ALTER TABLE error_logs NO FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY platform_admin_error_logs_select" in sql
    assert "ALTER TABLE permission_audit_log DISABLE ROW LEVEL SECURITY" in sql
