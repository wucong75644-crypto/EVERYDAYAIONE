"""Conversation Actor 生产契约补偿迁移测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "243_conversation_actor_contract_repair.sql"
ROLLBACK = MIGRATIONS / "rollback" / "243_conversation_actor_contract_repair_rollback.sql"


def test_repair_migration_restores_both_missing_actor_rpcs_atomically() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.lstrip().startswith("-- 243")
    assert "BEGIN;" in sql
    assert sql.rstrip().endswith("COMMIT;")
    assert "CREATE OR REPLACE FUNCTION public.cancel_paused_generation_turn(" in sql
    assert "CREATE OR REPLACE FUNCTION public.mark_stale_tool_invocation_uncertain(" in sql
    assert "p_stale_after_seconds INTEGER DEFAULT 900" in sql
    assert "status = 'uncertain'" in sql


def test_repair_rollback_does_not_remove_required_runtime_contract() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "non-destructive" in sql
    assert "DROP FUNCTION" not in sql
