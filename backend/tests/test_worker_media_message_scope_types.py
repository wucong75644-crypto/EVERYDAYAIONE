"""迁移 187：批次消息 Scope 比较必须兼容历史 VARCHAR 标识列。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
SQL = (
    MIGRATIONS / "187_worker_media_message_scope_types.sql"
).read_text()
ROLLBACK = (
    MIGRATIONS / "rollback"
    / "187_worker_media_message_scope_types_rollback.sql"
).read_text()


def test_scope_comparison_uses_text_on_both_sides() -> None:
    assert "p_message ->> 'id'" in SQL
    assert "v_task.placeholder_message_id::TEXT" in SQL
    assert "p_message ->> 'conversation_id'" in SQL
    assert "v_task.conversation_id::TEXT" in SQL
    assert "(p_message ->> 'id')::UUID IS DISTINCT FROM" not in SQL


def test_rollback_restores_previous_comparison() -> None:
    assert "(p_message ->> 'id')::UUID" in ROLLBACK
    assert "v_task.placeholder_message_id::TEXT" not in ROLLBACK
