"""Worker 活跃企业枚举窄能力迁移合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend/migrations/209_worker_active_organization_capability.sql"
)
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/"
    "209_worker_active_organization_capability_rollback.sql"
)


def test_rpc_is_owner_held_worker_scoped_and_returns_closed_result() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "SET LOCAL ROLE everydayai_owner;" in sql
    assert "CREATE FUNCTION worker_list_active_organization_ids()" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "session_user <> 'everydayai_worker'" in sql
    assert "'app.access_kind', TRUE" in sql
    assert "'app.actor_user_id', TRUE" in sql
    assert "'app.org_id', TRUE" in sql
    assert "'outcome', 'listed'" in sql
    assert "'organization_ids', v_organization_ids" in sql
    assert "organization.status = 'active'" in sql
    assert "ORDER BY organization.id" in sql


def test_worker_receives_only_rpc_execute_and_no_table_access() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "TO everydayai_worker;" in sql
    assert "GRANT SELECT" not in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
    for table in ("org_members", "org_configs"):
        assert table not in sql


def test_rollback_removes_only_the_rpc_capability() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "SET LOCAL ROLE everydayai_owner;" in sql
    assert "REVOKE EXECUTE ON FUNCTION" in sql
    assert "FROM everydayai_worker;" in sql
    assert "DROP FUNCTION worker_list_active_organization_ids();" in sql
    assert "GRANT " not in sql
    assert sql.rstrip().endswith("RESET ROLE;")
