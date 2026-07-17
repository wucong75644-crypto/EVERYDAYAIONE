"""Conversation Actor 原子终态迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "122_conversation_actor_terminal.sql"
ROLLBACK = MIGRATIONS / "rollback" / "122_conversation_actor_terminal_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(sql: str, name: str, next_name: str | None = None) -> str:
    start = sql.index(f"CREATE OR REPLACE FUNCTION {name}")
    end = sql.index(f"CREATE OR REPLACE FUNCTION {next_name}") if next_name else len(sql)
    return sql[start:end]


def test_commit_uses_consistent_lock_order_and_fencing() -> None:
    function = _function(
        _read(MIGRATION), "commit_generation_turn", "fail_generation_turn"
    )

    conversation_lock = function.index("SELECT * INTO v_conversation FROM conversations")
    task_lock = function.index("SELECT * INTO v_task FROM tasks")
    assert conversation_lock < task_lock
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in function
    assert "v_task.lease_expires_at <= NOW()" in function
    assert "'ownership_lost'" in function
    assert "'lease_expired'" in function


def test_commit_is_idempotent_and_deducts_before_closing_turn() -> None:
    function = _function(
        _read(MIGRATION), "commit_generation_turn", "fail_generation_turn"
    )

    assert "'already_committed'" in function
    deduct_position = function.index("SELECT deduct_credits_atomic")
    close_position = function.index("SELECT close_generation_turn")
    assert deduct_position < close_position
    assert "v_credit_result->>'success'" in function
    assert "ACTOR_COMMIT_INSUFFICIENT_CREDITS" in function
    assert "status = 'completed'" in function
    assert "active_serial_task_id = p_task_id" in function


def test_commit_validates_message_scope_and_json_shapes() -> None:
    function = _function(
        _read(MIGRATION), "commit_generation_turn", "fail_generation_turn"
    )

    assert "jsonb_typeof(p_result_content) <> 'array'" in function
    assert "jsonb_typeof(p_usage) <> 'object'" in function
    assert "p_credits_cost < 0" in function
    assert "v_output.reply_to_message_id IS DISTINCT FROM v_task.input_message_id" in function
    assert "v_output.turn_id IS DISTINCT FROM v_task.turn_id" in function


def test_fail_requires_owner_and_does_not_close_revision_or_charge() -> None:
    function = _function(
        _read(MIGRATION), "fail_generation_turn", "cancel_generation_turn"
    )

    assert function.index("SELECT * INTO v_conversation FROM conversations") < function.index(
        "SELECT * INTO v_task FROM tasks"
    )
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in function
    assert "'already_failed'" in function
    assert "status = 'failed'" in function
    assert "active_serial_task_id = p_task_id" in function
    assert "deduct_credits_atomic" not in function
    assert "close_generation_turn" not in function


def test_cancel_terminalizes_and_invalidates_running_owner() -> None:
    function = _function(_read(MIGRATION), "cancel_generation_turn")

    assert function.index("SELECT * INTO v_conversation FROM conversations") < function.index(
        "SELECT * INTO v_task FROM tasks"
    )
    assert "v_task.status NOT IN ('pending', 'running')" in function
    assert "v_task.user_id IS DISTINCT FROM p_user_id" in function
    assert "v_task.org_id IS DISTINCT FROM p_org_id" in function
    assert "delivery_context @> '{\"actor\": true}'::JSONB" in function
    assert "status = 'cancelled'" in function
    assert "execution_token = NULL" in function
    assert "lease_expires_at = NULL" in function
    assert "active_serial_task_id = p_task_id" in function
    assert "'already_cancelled'" in function


def test_terminal_rpcs_are_not_public() -> None:
    sql = _read(MIGRATION)

    for signature in (
        "fail_generation_turn(UUID, UUID, TEXT, TEXT)",
        "cancel_generation_turn(UUID, UUID, UUID)",
    ):
        assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC" in sql
    assert "REVOKE ALL ON FUNCTION commit_generation_turn(" in sql
    assert ") FROM PUBLIC;" in sql


def test_rollback_drops_all_terminal_rpcs() -> None:
    sql = _read(ROLLBACK)

    assert "DROP FUNCTION IF EXISTS cancel_generation_turn(UUID, UUID, UUID)" in sql
    assert "DROP FUNCTION IF EXISTS fail_generation_turn(UUID, UUID, TEXT, TEXT)" in sql
    assert "DROP FUNCTION IF EXISTS commit_generation_turn(" in sql
