"""RESUME must separate ReplayCheckpoint from the next delivery attempt."""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "244_conversation_actor_resume_delivery_reset.sql"
ROLLBACK = MIGRATIONS / "rollback" / "244_conversation_actor_resume_delivery_reset_rollback.sql"


def test_resume_resets_only_delivery_progress_and_invalidates_old_session():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "resume_paused_generation_turn" in sql
    assert "DELETE FROM conversation_delivery_sessions WHERE task_id = p_task_id" in sql
    assert "accumulated_content = ''" in sql
    assert "accumulated_blocks = '[]'::JSONB" in sql
    assert "UPDATE conversation_turn_checkpoints SET status = 'ready'" in sql
    assert "DELETE FROM messages" not in sql
    assert "DELETE FROM conversation_turn_checkpoints" not in sql


def test_resume_delivery_reset_rollback_does_not_restore_stale_replay():
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "non-destructive" in rollback
