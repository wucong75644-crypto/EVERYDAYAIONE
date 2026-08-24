"""Conversation Actor 页面交付会话迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "241_conversation_delivery_session.sql"
ROLLBACK = MIGRATIONS / "rollback" / "241_conversation_delivery_session_rollback.sql"


def test_delivery_session_has_fencing_stream_and_snapshot_contract() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS public.conversation_delivery_sessions" in sql
    assert "UNIQUE (task_id)" in sql
    assert "UNIQUE (stream_id)" in sql
    assert "execution_token UUID NOT NULL" in sql
    assert "execution_attempt INTEGER NOT NULL" in sql
    assert "snapshot_seq BIGINT NOT NULL" in sql
    assert "snapshot_blocks JSONB NOT NULL" in sql


def test_delivery_events_are_replayable_and_ordered() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS public.conversation_delivery_events" in sql
    assert "UNIQUE (stream_id, delivery_seq)" in sql
    assert "delivery_seq BIGINT NOT NULL" in sql
    assert "GREATEST(p_last_seq, v_session.snapshot_seq)" in sql
    assert "LIMIT 500" in sql
    assert "ORDER BY delivery_seq" in sql


def test_delivery_rpcs_fence_with_current_execution_token() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "begin_conversation_delivery_session" in sql
    assert "append_conversation_delivery_event" in sql
    assert "save_conversation_delivery_snapshot" in sql
    assert "read_conversation_delivery_state" in sql
    assert sql.count("execution_token IS DISTINCT FROM p_execution_token") >= 3
    assert "tasks_conversation_delivery_status_trigger" in sql


def test_rollback_drops_trigger_functions_then_tables() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert sql.index("DROP TRIGGER") < sql.index("DROP TABLE")
    assert "DROP TABLE IF EXISTS public.conversation_delivery_events" in sql
    assert "DROP TABLE IF EXISTS public.conversation_delivery_sessions" in sql
