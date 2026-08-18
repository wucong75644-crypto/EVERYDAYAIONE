"""父子任务关联与完成事件迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "140_conversation_subtasks.sql"
ROLLBACK = MIGRATIONS / "rollback" / "140_conversation_subtasks_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_subtask_link_has_parent_and_child_uniqueness():
    sql = _read(MIGRATION)
    assert "CREATE TABLE IF NOT EXISTS conversation_subtask_links" in sql
    assert "UNIQUE (parent_task_id, parent_command_id)" in sql
    assert "UNIQUE (child_task_id)" in sql
    assert "ACTOR_SUBTASK_SCOPE_MISMATCH" in sql


def test_child_terminal_transition_publishes_deduplicated_parent_event():
    sql = _read(MIGRATION)
    assert "publish_conversation_subtask_completion" in sql
    assert "NEW.status NOT IN ('completed', 'failed', 'cancelled')" in sql
    assert "'subtask_completed'" in sql
    assert "'subtask:' || NEW.id::TEXT" in sql
    assert "ON CONFLICT (task_id, dedupe_key) DO NOTHING" in sql
    assert "v_event_status" in sql


def test_rollback_keeps_pending_parent_child_links_safe():
    sql = _read(ROLLBACK)
    assert "ACTOR_SUBTASKS_PENDING" in sql
    assert "DROP TRIGGER IF EXISTS" in sql
    assert "DROP TABLE IF EXISTS conversation_subtask_links" in sql
