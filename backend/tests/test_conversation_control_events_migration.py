"""Conversation Actor 控制事件迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "138_conversation_control_events.sql"
ROLLBACK = MIGRATIONS / "rollback" / "138_conversation_control_events_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_control_event_table_has_dedupe_and_pending_indexes() -> None:
    sql = _read(MIGRATION)

    assert "CREATE TABLE IF NOT EXISTS conversation_control_events" in sql
    assert "event_sequence BIGINT NOT NULL" in sql
    assert "UNIQUE (task_id, dedupe_key)" in sql
    assert "status IN ('pending', 'applied', 'ignored')" in sql
    assert "idx_conversation_control_events_pending" in sql
    assert "jsonb_typeof(payload) = 'object'" in sql


def test_append_is_scoped_and_idempotent() -> None:
    sql = _read(MIGRATION)
    start = sql.index("CREATE OR REPLACE FUNCTION append_conversation_control_command")
    end = sql.index("CREATE OR REPLACE FUNCTION read_conversation_control_commands")
    function = sql[start:end]

    assert "FOR UPDATE" in function
    assert "v_task.conversation_id IS DISTINCT FROM p_conversation_id" in function
    assert "delivery_context @> '{\"actor\": true}'::JSONB" in function
    assert "p_event_type = 'approval_result'" in function
    assert "ACTOR_CONTROL_EVENT_TASK_NOT_RUNNING" in function
    assert "ON CONFLICT (task_id, dedupe_key) DO NOTHING" in function
    assert "already_enqueued" in function
    assert "'payload', v_event.payload" in function


def test_read_and_ack_require_current_fencing_owner() -> None:
    sql = _read(MIGRATION)
    read = sql[sql.index("CREATE OR REPLACE FUNCTION read_conversation_control_commands"):]
    ack = sql[sql.index("CREATE OR REPLACE FUNCTION acknowledge_conversation_control_command"):]

    assert "v_task.status <> 'running'" in read
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in read
    assert "status = 'pending'" in read
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in ack
    assert "'ownership_lost'" in ack
    assert "'already_acknowledged'" in ack


def test_cancel_trigger_writes_deduplicated_control_event() -> None:
    sql = _read(MIGRATION)

    assert "create_actor_cancel_control_command" in sql
    assert "NEW.status = 'cancelled'" in sql
    assert "'cancel:' || NEW.id::TEXT" in sql
    assert "tasks_actor_cancel_control_event_trigger" in sql


def test_control_event_append_supports_tool_completion() -> None:
    sql = _read(MIGRATION)
    assert "'tool_completed'" in sql
    assert "p_event_type = 'approval_result'" in sql


def test_steer_migration_reuses_control_event_table() -> None:
    migration = _read(MIGRATIONS / "242_conversation_actor_steer.sql")

    assert "ALTER TABLE conversation_control_events" in migration
    assert "'pause', 'resume'" in migration
    assert "'steer'" in migration
    assert "CREATE OR REPLACE FUNCTION append_conversation_steer" in migration
    assert "ACTOR_STEER_SCOPE_MISMATCH" in migration
    assert "CREATE TABLE" not in migration


def test_rollback_refuses_pending_events_and_drops_objects_in_order() -> None:
    sql = _read(ROLLBACK)

    assert "ACTOR_CONTROL_EVENTS_PENDING" in sql
    assert "DROP TRIGGER IF EXISTS" in sql
    assert "DROP FUNCTION IF EXISTS create_actor_cancel_control_command()" in sql
    assert "DROP TABLE IF EXISTS conversation_control_events" in sql
    assert "DROP SEQUENCE IF EXISTS conversation_control_event_sequence_seq" in sql
