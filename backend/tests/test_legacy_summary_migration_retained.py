"""旧摘要数据库合同仅为回滚保留，不得重新接入运行时。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"


def test_legacy_summary_rpc_migration_remains_reversible() -> None:
    migration = (
        MIGRATIONS / "137_context_summary_revision_rpc.sql"
    ).read_text(encoding="utf-8")
    rollback = (
        MIGRATIONS / "rollback/137_context_summary_revision_rpc_rollback.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION apply_context_summary" in migration
    assert "SECURITY INVOKER" in migration
    assert "DROP FUNCTION IF EXISTS apply_context_summary" in rollback
    assert "DROP COLUMN" not in rollback
