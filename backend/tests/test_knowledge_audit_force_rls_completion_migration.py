from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND_DIR / "migrations"
    / "202_knowledge_audit_force_rls_completion.sql"
)
ROLLBACK = (
    BACKEND_DIR / "migrations" / "rollback"
    / "202_knowledge_audit_force_rls_completion_rollback.sql"
)
PREFLIGHT = (
    BACKEND_DIR.parent / "deploy" / "preflight"
    / "knowledge-audit-completion.sh"
)
TABLES = (
    "knowledge_nodes",
    "knowledge_edges",
    "knowledge_metrics",
    "scoring_audit_log",
    "tool_audit_log",
)


def test_completion_forces_rls_with_owner_policy_on_each_table() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for table in TABLES:
        assert f"CREATE POLICY {table}_owner_all ON {table}" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql


def test_completion_preserves_only_runtime_knowledge_table_surface() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in sql
    assert "everydayai_worker, everydayai_sync" in sql
    assert "REVOKE ALL PRIVILEGES (%s) ON TABLE public.%I" in sql
    assert (
        "GRANT SELECT, INSERT, UPDATE ON knowledge_nodes "
        "TO everydayai_runtime"
    ) in sql
    assert (
        "GRANT SELECT, INSERT, UPDATE ON knowledge_edges "
        "TO everydayai_runtime"
    ) in sql
    assert (
        "GRANT INSERT ON knowledge_metrics TO everydayai_runtime"
    ) in sql
    assert "GRANT" not in sql.split("scoring_audit_log, tool_audit_log", 1)[-1].split(
        "-- Runtime", 1
    )[0]


def test_rollback_removes_force_and_owner_policies_only() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    for table in TABLES:
        assert f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY" in sql
        assert f"DROP POLICY {table}_owner_all ON {table}" in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql


def test_preflight_checks_acl_owner_policies_and_force_rls() -> None:
    script = PREFLIGHT.read_text(encoding="utf-8")

    assert "KNOWLEDGE_AUDIT_SERVICE_ACL_INVALID" in script
    assert "KNOWLEDGE_AUDIT_OWNER_POLICY_INVALID" in script
    assert "KNOWLEDGE_AUDIT_FORCE_RLS_INVALID" in script
    assert "has_any_column_privilege(" in script
    for table in TABLES:
        assert f"'public.{table}'::regclass" in script
