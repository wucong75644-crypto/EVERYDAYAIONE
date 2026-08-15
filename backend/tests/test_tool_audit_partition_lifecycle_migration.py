"""229 工具审计分区生命周期迁移合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "229_tool_audit_partition_lifecycle.sql"
ROLLBACK = (
    ROOT
    / "migrations"
    / "rollback"
    / "229_tool_audit_partition_lifecycle_rollback.sql"
)


def test_migration_owns_partition_lifecycle_at_write_boundary() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "FOR month_offset IN 0..2 LOOP" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "TOOL_AUDIT_PARTITION_OWNER_INVALID" in sql
    assert "TOOL_AUDIT_PARTITION_BOUND_INVALID" in sql
    assert "PERFORM public.maintain_tool_audit_partitions();" in sql
    assert sql.index("PERFORM public.maintain_tool_audit_partitions();") < sql.index(
        "INSERT INTO public.tool_audit_log"
    )
    assert "CURRENT_DATE - INTERVAL '90 days'" in sql
    assert "GRANT EXECUTE ON FUNCTION maintain_tool_audit_partitions()" not in sql
    assert "TO everydayai_runtime;" in sql


def test_rollback_restores_previous_function_without_dropping_new_partitions() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "FOR i IN 1..2 LOOP" in sql
    assert "PERFORM public.maintain_tool_audit_partitions();" not in sql
    assert "DROP TABLE tool_audit_log_" not in sql
    assert "GRANT EXECUTE ON FUNCTION maintain_tool_audit_partitions() TO everydayai;" in sql
