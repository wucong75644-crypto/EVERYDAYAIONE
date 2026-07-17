"""Conversation Actor 队列迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "121_conversation_actor_queue.sql"
ROLLBACK = MIGRATIONS / "rollback" / "121_conversation_actor_queue_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_migration_adds_compatible_actor_fields_and_indexes() -> None:
    sql = _read(MIGRATION)

    for field in (
        "queue_sequence BIGINT",
        "execution_token UUID",
        "lease_expires_at TIMESTAMPTZ",
        "execution_attempt INTEGER NOT NULL DEFAULT 0",
        "delivery_context JSONB NOT NULL DEFAULT '{}'::JSONB",
        "terminal_reason TEXT",
        "active_serial_task_id UUID",
        "actor_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ):
        assert field in sql
    assert "tasks_execution_attempt_check" in sql
    assert "tasks_delivery_context_object_check" in sql
    assert "idx_tasks_actor_queue" in sql
    assert "idx_tasks_actor_expired_lease" in sql
    assert "idx_tasks_actor_active" in sql


def test_queue_sequence_backfill_precedes_constraints_and_owns_sequence() -> None:
    sql = _read(MIGRATION)

    backfill_position = sql.index(
        "SET queue_sequence = nextval('task_queue_sequence_seq')"
    )
    not_null_position = sql.index("ALTER COLUMN queue_sequence SET NOT NULL")
    assert backfill_position < not_null_position
    assert "ALTER SEQUENCE task_queue_sequence_seq OWNED BY tasks.queue_sequence" in sql
    assert "FOREIGN KEY (active_serial_task_id) REFERENCES tasks(id) ON DELETE SET NULL" in sql


def test_enqueue_is_pending_idempotent_and_does_not_bind_revision() -> None:
    sql = _read(MIGRATION)
    start = sql.index("CREATE OR REPLACE FUNCTION enqueue_generation_turn")
    end = sql.index("CREATE OR REPLACE FUNCTION claim_next_serial_generation_turn")
    function = sql[start:end]

    assert "'chat'," in function
    assert "'pending'," in function
    assert "ON CONFLICT (id) DO NOTHING" in function
    assert "GET DIAGNOSTICS v_inserted_count = ROW_COUNT" in function
    assert "'already_enqueued', v_inserted_count = 0" in function
    assert "ACTOR_ENQUEUE_CONFLICT" in function
    assert "reply_to_message_id = p_input_message_id" in function
    assert "base_context_revision" not in function
    assert "context_through_message_id" not in function
    assert "SECURITY INVOKER" in function


def test_serial_claim_locks_conversation_and_claims_oldest_pending() -> None:
    sql = _read(MIGRATION)
    start = sql.index("CREATE OR REPLACE FUNCTION claim_next_serial_generation_turn")
    end = sql.index("CREATE OR REPLACE FUNCTION claim_branch_generation_turn")
    function = sql[start:end]

    assert "FROM conversations" in function
    assert "FOR UPDATE" in function
    assert "active_serial_task_id" in function
    assert "lease_expires_at > NOW()" in function
    assert "ORDER BY queue_sequence, id" in function
    assert "FOR UPDATE SKIP LOCKED" in function
    assert "execution_attempt >= p_max_attempts" in function
    assert "status = 'pending'" in function
    assert "status = 'running'" in function
    assert "delivery_context @> '{\"actor\": true}'::JSONB" in function
    assert "base_context_revision = v_conversation.context_revision" in function
    assert "context_through_message_id = v_conversation.last_closed_message_id" in function


def test_branch_claim_does_not_modify_serial_owner() -> None:
    sql = _read(MIGRATION)
    start = sql.index("CREATE OR REPLACE FUNCTION claim_branch_generation_turn")
    end = sql.index("CREATE OR REPLACE FUNCTION renew_generation_lease")
    function = sql[start:end]

    assert "execution_mode <> 'branch'" in function
    assert "delivery_context @> '{\"actor\": true}'::JSONB" in function
    assert "base_context_revision = v_conversation.context_revision" in function
    assert "lease_expires_at > NOW()" in function
    assert "execution_attempt >= p_max_attempts" in function
    assert "'attempts_exhausted'" in function
    assert "active_serial_task_id" not in function


def test_renew_requires_current_fencing_token() -> None:
    sql = _read(MIGRATION)
    start = sql.index("CREATE OR REPLACE FUNCTION renew_generation_lease")
    function = sql[start:]

    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in function
    assert "'ownership_lost'" in function
    assert "'terminal'" in function
    assert "'renewed'" in function
    assert "p_lease_seconds NOT BETWEEN 15 AND 300" in function


def test_rpcs_are_not_public() -> None:
    sql = _read(MIGRATION)

    for signature in (
        "enqueue_generation_turn(JSONB, UUID, UUID, TEXT, JSONB)",
        "claim_next_serial_generation_turn(UUID, INTEGER, INTEGER)",
        "claim_branch_generation_turn(UUID, INTEGER, INTEGER)",
        "renew_generation_lease(UUID, UUID, INTEGER)",
    ):
        assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC" in sql


def test_rollback_removes_rpcs_before_actor_columns() -> None:
    sql = _read(ROLLBACK)

    function_position = sql.index("DROP FUNCTION IF EXISTS renew_generation_lease")
    column_position = sql.index("DROP COLUMN IF EXISTS execution_token")
    assert function_position < column_position
    assert "DROP CONSTRAINT IF EXISTS conversations_active_serial_task_id_fkey" in sql
    assert "DROP SEQUENCE IF EXISTS task_queue_sequence_seq" in sql
