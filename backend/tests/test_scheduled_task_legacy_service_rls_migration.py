"""定时任务传统服务 RLS 边界的迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "243_scheduled_task_legacy_service_rls.sql"
ROLLBACK = MIGRATIONS / "rollback/243_scheduled_task_legacy_service_rls_rollback.sql"


def test_all_scheduled_task_tables_have_forced_service_policy() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for table in (
        "scheduled_tasks",
        "scheduled_task_runs",
        "scheduled_task_deliveries",
    ):
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY {table}_legacy_service" in sql

    assert sql.count("FOR ALL TO everydayai") == 3
    assert sql.count("SESSION_USER = 'everydayai'") == 6


def test_rollback_restores_a_usable_pre_migration_state() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")

    for table in (
        "scheduled_tasks",
        "scheduled_task_runs",
        "scheduled_task_deliveries",
    ):
        assert f"DROP POLICY IF EXISTS {table}_legacy_service" in rollback
    assert "ALTER TABLE public.scheduled_tasks NO FORCE ROW LEVEL SECURITY" in rollback
    assert "ALTER TABLE public.scheduled_task_runs NO FORCE ROW LEVEL SECURITY" in rollback
    assert "ALTER TABLE public.scheduled_task_deliveries DISABLE ROW LEVEL SECURITY" in rollback
