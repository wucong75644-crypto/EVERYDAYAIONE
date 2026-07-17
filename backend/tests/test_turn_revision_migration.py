"""Turn/revision 数据库基础迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "120_turn_revision_foundation.sql"
ROLLBACK = MIGRATIONS / "rollback" / "120_turn_revision_foundation_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_migration_adds_compatible_turn_revision_fields() -> None:
    sql = _read(MIGRATION)

    for field in (
        "turn_id UUID",
        "reply_to_message_id UUID",
        "context_revision BIGINT",
        "message_kind TEXT NOT NULL DEFAULT 'conversation'",
        "input_message_id UUID",
        "base_context_revision BIGINT",
        "context_through_message_id UUID",
        "execution_mode TEXT NOT NULL DEFAULT 'serial'",
        "last_closed_message_id UUID",
        "summary_revision BIGINT NOT NULL DEFAULT 0",
        "summary_through_message_id UUID",
    ):
        assert field in sql


def test_migration_defines_revision_and_scope_indexes() -> None:
    sql = _read(MIGRATION)

    assert "idx_messages_conversation_revision_created" in sql
    assert "ON messages(conversation_id, context_revision, created_at)" in sql
    assert "idx_messages_conversation_turn" in sql
    assert "idx_messages_reply_to" in sql
    assert "idx_tasks_conversation_turn" in sql
    assert "idx_tasks_input_message" in sql


def test_bind_rpc_locks_scope_and_is_idempotent() -> None:
    sql = _read(MIGRATION)

    assert "CREATE OR REPLACE FUNCTION bind_generation_turn" in sql
    assert "FROM conversations" in sql and "FOR UPDATE" in sql
    assert "v_input.role::TEXT <> 'user'" in sql
    assert "v_output.role::TEXT <> 'assistant'" in sql
    assert "TURN_MESSAGE_RELATION_MISMATCH" in sql
    assert "reply_to_message_id = p_input_message_id" in sql
    assert "v_task.org_id IS DISTINCT FROM v_conversation.org_id" in sql
    assert "TURN_ALREADY_BOUND" in sql
    assert "p_execution_mode IS NULL" in sql
    assert "base_context_revision = v_conversation.context_revision" in sql
    assert "RETURNING * INTO v_task" in sql
    assert "'context_through_message_id', v_task.context_through_message_id" in sql
    assert "SECURITY INVOKER" in sql


def test_close_rpc_advances_once_and_preserves_reply_binding() -> None:
    sql = _read(MIGRATION)

    assert "CREATE OR REPLACE FUNCTION close_generation_turn" in sql
    assert "v_output.reply_to_message_id IS DISTINCT FROM v_task.input_message_id" in sql
    assert "v_task.assistant_message_id IS DISTINCT FROM p_output_message_id" in sql
    assert "IF v_output.context_revision IS NOT NULL" in sql
    assert "v_closed_revision := v_conversation.context_revision + 1" in sql
    assert "AND context_revision IS NULL" in sql
    assert "SET assistant_message_id = p_output_message_id" in sql
    assert "status = 'completed'" in sql
    assert "completed_at = COALESCE(completed_at, NOW())" in sql
    assert "last_closed_message_id = p_output_message_id" in sql
    assert "'already_closed', TRUE" in sql
    assert "REVOKE ALL ON FUNCTION close_generation_turn" in sql


def test_constraints_reject_invalid_revisions_kinds_and_modes() -> None:
    sql = _read(MIGRATION)

    assert "context_revision IS NULL OR context_revision >= 0" in sql
    assert "message_kind IN ('conversation', 'synthetic', 'tool_internal')" in sql
    assert "base_context_revision IS NULL OR base_context_revision >= 0" in sql
    assert "execution_mode IN ('serial', 'branch')" in sql
    assert "summary_revision >= 0" in sql


def test_rollback_drops_rpcs_before_columns() -> None:
    sql = _read(ROLLBACK)

    close_position = sql.index("DROP FUNCTION IF EXISTS close_generation_turn")
    bind_position = sql.index("DROP FUNCTION IF EXISTS bind_generation_turn")
    column_position = sql.index("DROP COLUMN IF EXISTS context_revision")
    assert close_position < column_position
    assert bind_position < column_position
    assert "DROP INDEX IF EXISTS idx_messages_conversation_revision_created" in sql
