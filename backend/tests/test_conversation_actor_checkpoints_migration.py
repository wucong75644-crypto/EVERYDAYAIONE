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


def test_control_append_has_org_scoped_api_overload_and_rollback():
    sql = MIGRATION.read_text(encoding="utf-8")
    rollback_path = MIGRATION.parent / "rollback" / (
        "142_conversation_actor_turn_checkpoints_rollback.sql"
    )
    rollback = rollback_path.read_text(encoding="utf-8")

    assert "p_org_id UUID" in sql
    assert "v_task.org_id IS DISTINCT FROM p_org_id" in sql
    assert "v_conversation.org_id IS DISTINCT FROM p_org_id" in sql
    assert "JSONB, UUID" in sql
    assert "append_conversation_control_command(UUID, UUID, UUID, TEXT, TEXT, JSONB, UUID)" in rollback
