from pathlib import Path


MIGRATION = Path(__file__).parents[1] / (
    "migrations/238_restore_legacy_tool_audit_insert_access.sql"
)
ROLLBACK = Path(__file__).parents[1] / (
    "migrations/rollback/238_restore_legacy_tool_audit_insert_access_rollback.sql"
)


def test_migration_restores_only_application_audit_access():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "legacy_app_tool_audit_all" in sql
    assert "ON tool_audit_log" in sql
    assert "FOR ALL TO everydayai" in sql
    assert "SET LOCAL ROLE everydayai_owner" in sql
    assert "USING (FALSE)" in sql
    assert "memory_" not in sql
    assert "everydayai_runtime" not in sql


def test_rollback_removes_only_application_audit_policy():
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "DROP POLICY IF EXISTS legacy_app_tool_audit_all" in sql
    assert "DROP FUNCTION" not in sql
    assert "DROP TABLE" not in sql
