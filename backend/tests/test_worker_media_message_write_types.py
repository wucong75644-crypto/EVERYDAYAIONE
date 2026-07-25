"""迁移 188：批次消息写入必须显式匹配生产列类型。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
SQL = (
    MIGRATIONS / "188_worker_media_message_write_types.sql"
).read_text()
ROLLBACK = (
    MIGRATIONS / "rollback"
    / "188_worker_media_message_write_types_rollback.sql"
).read_text()


def test_message_write_uses_explicit_target_types() -> None:
    assert "'assistant'::public.message_role" in SQL
    assert "COALESCE(p_message -> 'content', '[]'::JSONB)::TEXT" in SQL
    assert "(p_message ->> 'task_id')::UUID" in SQL


def test_rollback_restores_previous_implicit_write() -> None:
    assert "(p_message ->> 'task_id')::UUID" not in ROLLBACK
