"""Runtime 工具审计能力迁移合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/196_runtime_tool_audit_capability.sql"
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/196_runtime_tool_audit_capability_rollback.sql"
)


def test_capability_binds_audit_identity_to_scoped_task() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "SECURITY DEFINER" in sql
    assert "session_user <> 'everydayai_runtime'" in sql
    assert "current_setting('app.access_kind', TRUE) <> 'runtime'" in sql
    assert "task.user_id = tenant_actor_user_id()" in sql
    assert "task.org_id IS NOT DISTINCT FROM tenant_org_id()" in sql
    assert "v_task.conversation_id::TEXT" in sql
    assert "COALESCE(v_task.org_id::TEXT, '')" in sql
    assert "TOOL_AUDIT_TASK_ACCESS_DENIED" in sql


def test_capability_has_narrow_grant_and_owner_partition_maintenance() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "TO everydayai_runtime;" in sql
    assert "GRANT INSERT ON" not in sql
    assert "ALTER FUNCTION maintain_tool_audit_partitions() SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "GRANT EXECUTE ON FUNCTION maintain_tool_audit_partitions()" in sql


def test_rollback_removes_capability_and_restores_invoker() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "DROP FUNCTION record_runtime_tool_audit(" in sql
    assert "maintain_tool_audit_partitions() SECURITY INVOKER" in sql
