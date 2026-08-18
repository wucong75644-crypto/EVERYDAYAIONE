"""Migration 142 的发布契约测试。"""

from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations" / (
    "142_conversation_actor_turn_checkpoints.sql"
)


def test_checkpoint_migration_contains_pause_resume_contracts():
    sql = MIGRATION.read_text(encoding="utf-8")

    for function_name in (
        "save_generation_checkpoint",
        "load_generation_checkpoint",
        "pause_generation_turn_owned",
        "resume_paused_generation_turn",
    ):
        assert f"FUNCTION {function_name}" in sql
    assert "conversation_turn_checkpoints" in sql
    assert "status IN ('pending', 'running', 'paused'" in sql
    assert "'pause', 'resume'" in sql
