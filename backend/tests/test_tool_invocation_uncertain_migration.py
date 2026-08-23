"""工具副作用恢复迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "239_conversation_actor_production_contract_compat.sql"
ROLLBACK = MIGRATIONS / "rollback" / "239_conversation_actor_production_contract_compat_rollback.sql"


def test_stale_invocation_migration_is_fenced_and_conservative() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "mark_stale_tool_invocation_uncertain" in sql
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in sql
    assert "status = 'uncertain'" in sql
    assert "禁止自动重试" in sql
    assert "make_interval(secs => v_threshold)" in sql


def test_rollback_only_removes_helper_function() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "DROP FUNCTION IF EXISTS public.mark_stale_tool_invocation_uncertain" in sql
    assert "DROP TABLE" not in sql
