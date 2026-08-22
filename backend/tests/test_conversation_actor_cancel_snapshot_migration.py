"""Conversation Actor 取消快照迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "141_conversation_actor_cancel_snapshot.sql"
ROLLBACK = MIGRATIONS / "rollback" / "141_conversation_actor_cancel_snapshot_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(sql: str) -> str:
    start = sql.index("CREATE OR REPLACE FUNCTION cancel_generation_turn")
    return sql[start:]


def test_cancel_materializes_progress_before_terminalizing_task() -> None:
    function = _function(_read(MIGRATION))

    assert function.index("SELECT * INTO v_conversation FROM conversations") < function.index(
        "SELECT * INTO v_task FROM tasks"
    )
    assert function.index("UPDATE messages") < function.index("UPDATE tasks")
    assert "v_task.accumulated_content" in function
    assert "v_task.accumulated_blocks" in function
    assert "merge_blocks_with_text" in _read(MIGRATION)
    assert "jsonb_array_elements(v_snapshot)" in function
    assert "'interrupt_marker'" in function
    assert "'user_cancel'" in function
    assert "'snapshot_saved'" in function


def test_cancel_marks_running_tool_steps_as_cancelled() -> None:
    function = _function(_read(MIGRATION))

    assert "item.value->>'type' = 'tool_step'" in function
    assert "item.value->>'status' = 'running'" in function
    assert "'status', 'cancelled'" in function
    assert "'cancelled_at', v_now_iso" in function


def test_cancel_keeps_scope_checks_and_idempotency() -> None:
    function = _function(_read(MIGRATION))

    assert "delivery_context @> '{\"actor\": true}'::JSONB" in function
    assert "v_task.user_id IS DISTINCT FROM p_user_id" in function
    assert "v_task.org_id IS DISTINCT FROM p_org_id" in function
    assert "v_conversation.org_id IS DISTINCT FROM p_org_id" in function
    assert "v_task.status = 'cancelled'" in function
    assert "'already_cancelled'" in function
    assert "execution_token = NULL" in function
    assert "lease_expires_at = NULL" in function


def test_rollback_restores_the_previous_cancel_contract() -> None:
    sql = _read(ROLLBACK)

    assert "CREATE OR REPLACE FUNCTION cancel_generation_turn" in sql
    assert "UPDATE messages" in sql
    assert "status = 'interrupted'" in sql
    assert "accumulated_blocks" not in sql
    assert "interrupt_marker" not in sql
