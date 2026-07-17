"""Conversation Actor fencing 进度迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "123_conversation_actor_progress.sql"
ROLLBACK = MIGRATIONS / "rollback" / "123_conversation_actor_progress_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_progress_requires_current_running_owner_and_live_lease() -> None:
    sql = _read(MIGRATION)

    assert "CREATE OR REPLACE FUNCTION update_generation_progress" in sql
    assert "v_task.status <> 'running'" in sql
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in sql
    assert "v_task.lease_expires_at IS NULL" in sql
    assert "v_task.lease_expires_at <= NOW()" in sql
    assert "'ownership_lost'" in sql
    assert "'lease_expired'" in sql


def test_progress_validates_shapes_and_updates_only_task_accumulation() -> None:
    sql = _read(MIGRATION)

    assert "jsonb_typeof(p_accumulated_blocks) <> 'array'" in sql
    assert "accumulated_content = p_accumulated_content" in sql
    assert "accumulated_blocks = p_accumulated_blocks" in sql
    assert "WHERE id = p_task_id" in sql
    assert "UPDATE messages" not in sql
    assert "UPDATE conversations" not in sql


def test_progress_rpc_is_not_public_and_rollback_drops_it() -> None:
    sql = " ".join(_read(MIGRATION).split())
    rollback = " ".join(_read(ROLLBACK).split())
    signature = "update_generation_progress(UUID, UUID, TEXT, JSONB)"

    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC" in sql
    assert f"DROP FUNCTION IF EXISTS {signature}" in rollback
