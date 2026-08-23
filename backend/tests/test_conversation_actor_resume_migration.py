"""Conversation Actor RESUME 迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "239_conversation_actor_production_contract_compat.sql"
ROLLBACK = MIGRATIONS / "rollback" / "239_conversation_actor_production_contract_compat_rollback.sql"


def test_resume_requeues_paused_task_and_requires_checkpoint():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "resume_paused_generation_turn" in sql
    assert "v_task.status <> 'paused'" in sql
    assert "conversation_turn_checkpoints" in sql
    assert "status = 'pending'" in sql
    assert "execution_token = NULL" in sql
    assert "ACTOR_RESUME_CHECKPOINT_MISSING" in sql


def test_resume_has_new_control_event_and_rollback_guard():
    sql = MIGRATION.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert "'resume'" in sql
    assert "resume:" in sql
    assert "non-destructive" in rollback
