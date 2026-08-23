"""Conversation Actor PAUSE 快照迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "239_conversation_actor_production_contract_compat.sql"
ROLLBACK = MIGRATIONS / "rollback" / "239_conversation_actor_production_contract_compat_rollback.sql"


def _read() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_pause_status_and_control_event_are_declared():
    sql = _read()
    assert "'paused'" in sql
    assert "'pause'" in sql
    assert "pause_generation_turn_owned" in sql
    assert "cancel_generation_turn_owned" in sql
    assert "cancel_paused_generation_turn" in sql
    assert "materialize_actor_pause_snapshot" in sql
    assert "conversation_turn_checkpoints" in sql
    assert "conversation_replay_checkpoints" not in sql


def test_running_control_requests_are_deferred_to_runtime():
    sql = _read()
    assert "append_conversation_control_command" in sql
    assert "INSERT INTO conversation_control_events" in sql
    assert "'outcome', 'enqueued'" in sql


def test_owner_control_persists_message_before_task_terminal_state():
    sql = _read()
    assert sql.index("UPDATE messages") < sql.index("UPDATE tasks")
    assert "v_task.accumulated_content" in sql
    assert "v_task.accumulated_blocks" in sql
    assert "'user_paused'" in sql
    assert "'user_pause'" in sql
    assert "execution_token = NULL" in sql


def test_cancel_after_pause_is_still_a_final_cancel():
    sql = _read()

    assert "status = 'paused'" in sql
    assert "cancel_generation_turn_owned" in sql
    assert "ACTOR_PAUSE_CHECKPOINT_MISSING" in sql


def test_rollback_restores_pre_pause_contract():
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "non-destructive" in sql
    assert "DROP FUNCTION IF EXISTS public.mark_stale_tool_invocation_uncertain" in sql
    assert "DROP TABLE" not in sql


def test_text_content_compatibility_migration_is_present():
    migration = MIGRATIONS / "240_conversation_actor_text_content_compat.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "actor_message_text_to_blocks" in sql
    assert "v_message.content" in sql
    assert "v_content::TEXT" in sql
    assert "DatatypeMismatch" in sql
    assert "COALESCE(v_message.content, '[]'::JSONB)" not in sql
    assert "ALTER TABLE" not in sql
