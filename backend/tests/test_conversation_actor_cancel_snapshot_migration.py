"""Conversation Actor 取消快照迁移契约测试。"""

from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations/141_conversation_actor_cancel_snapshot.sql"
)


def _read() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_cancel_snapshot_materializes_accumulated_progress() -> None:
    sql = _read()
    assert "materialize_actor_cancel_snapshot" in sql
    assert "v_task.accumulated_blocks" in sql
    assert "v_task.accumulated_content" in sql
    assert "status = 'interrupted'" in sql
    assert "interrupt_marker" in sql


def test_owned_cancel_requires_current_fencing_token() -> None:
    sql = _read()
    start = sql.index("CREATE OR REPLACE FUNCTION cancel_generation_turn_owned")
    end = sql.index("-- 保持旧用户范围取消入口", start)
    function = sql[start:end]
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in function
    assert "execution_token = NULL" in function
    assert "status = 'cancelled'" in function
    assert "snapshot_saved" in function


def test_cancel_trigger_converges_pending_event_to_applied() -> None:
    sql = _read()
    assert "ON CONFLICT (task_id, dedupe_key) DO UPDATE" in sql
    assert "SET status = 'applied', applied_at = NOW()" in sql
