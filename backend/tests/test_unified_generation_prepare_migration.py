"""统一生成 Turn 准备迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "148_unified_generation_prepare.sql"
HOTFIX_MIGRATION = MIGRATIONS / "149_generation_message_content_type.sql"
ROLLBACK = MIGRATIONS / "rollback" / "148_unified_generation_prepare_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(sql: str, name: str, next_name: str | None = None) -> str:
    start = sql.index(f"CREATE OR REPLACE FUNCTION {name}")
    if next_name is None:
        return sql[start:]
    return sql[start:sql.index(f"CREATE OR REPLACE FUNCTION {next_name}")]


def test_migration_adds_preparing_state_and_external_id_uniqueness() -> None:
    sql = _read(MIGRATION)

    assert "DROP CONSTRAINT IF EXISTS tasks_status_check" in sql
    assert "'preparing', 'pending', 'running', 'completed', 'failed', 'cancelled'" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_external_task_id" in sql
    assert "WHERE external_task_id IS NOT NULL" in sql
    assert "DROP INDEX IF EXISTS uq_credit_tx_task_org" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_tx_pending_task_org" in sql
    assert "WHERE type = 'lock' AND status = 'pending'" in sql


def test_prepare_locks_scope_in_fixed_order_and_is_bounded() -> None:
    sql = _read(MIGRATION)
    messages = _function(sql, "_prepare_generation_messages", "_prepare_generation_tasks")
    tasks = _function(sql, "_prepare_generation_tasks", "prepare_generation")
    function = _function(sql, "prepare_generation", "attach_generation_external_task")

    request_lock = function.index("FROM message_generation_requests")
    conversation_lock = function.index("FROM conversations")
    messages_call = function.index("_prepare_generation_messages(")
    tasks_call = function.index("_prepare_generation_tasks(")
    assert request_lock < conversation_lock < messages_call < tasks_call
    assert "FROM messages WHERE id = v_input_id FOR UPDATE" in messages
    assert "FROM tasks WHERE id = v_id FOR UPDATE" in tasks
    assert "jsonb_array_length(p_tasks) NOT BETWEEN 1 AND 16" in function
    assert "v_request.org_id IS DISTINCT FROM p_org_id" in function
    assert "v_conversation.org_id IS DISTINCT FROM p_org_id" in function
    assert "SECURITY INVOKER" in function


def test_prepare_uses_explicit_retry_anchor_and_never_time_fallback() -> None:
    function = _function(
        _read(MIGRATION), "_prepare_generation_messages", "_prepare_generation_tasks"
    )

    assert "v_output.reply_to_message_id IS NOT NULL" in function
    assert "assistant_message_id = v_output_id" in function
    assert "GENERATION_PREPARE_ANCHOR_MISSING" in function
    assert "ORDER BY created_at DESC" in function
    assert "messages u" not in function
    assert "uuid_generate" not in function
    assert "gen_random_uuid" not in function


def test_retry_accepts_anchor_only_input_and_resets_failed_output_atomically() -> None:
    function = _function(
        _read(MIGRATION), "_prepare_generation_messages", "_prepare_generation_tasks"
    )

    assert "p_operation NOT IN ('retry', 'regenerate_single') AND v_input_id IS NULL" in function
    assert "p_operation = 'retry' AND v_output.status::TEXT <> 'failed'" in function
    assert "status IN ('preparing', 'pending', 'running')" in function
    assert "THEN COALESCE((p_output_message->'content')::TEXT, v_output.content)" in function
    assert "p_output_message->'generation_params', v_output.generation_params" in function
    assert "THEN FALSE" in function


def test_hotfix_replaces_function_with_explicit_jsonb_to_text_boundary() -> None:
    sql = _read(HOTFIX_MIGRATION)

    assert "CREATE OR REPLACE FUNCTION _prepare_generation_messages(" in sql
    assert "COALESCE((p_output_message->'content')::TEXT, v_output.content)" in sql
    assert "COALESCE(p_output_message->'content', v_output.content)" not in sql
    assert "REVOKE ALL ON FUNCTION _prepare_generation_messages(" in sql


def test_prepare_creates_messages_tasks_and_returns_authoritative_anchor() -> None:
    sql = _read(MIGRATION)
    messages = _function(sql, "_prepare_generation_messages", "_prepare_generation_tasks")
    tasks = _function(sql, "_prepare_generation_tasks", "prepare_generation")
    function = _function(sql, "prepare_generation", "attach_generation_external_task")

    assert messages.count("ON CONFLICT (id) DO NOTHING") == 2
    assert tasks.count("ON CONFLICT (id) DO NOTHING") == 1
    assert "v_task.external_task_id IS DISTINCT FROM" in tasks
    assert "v_task.delivery_context IS DISTINCT FROM" in tasks
    assert "v_task.request_params IS DISTINCT FROM" in tasks
    assert "reply_to_message_id = v_input_id" in messages
    assert "base_context_revision" in function
    assert "context_through_message_id" in function
    assert "UPDATE message_generation_requests" in function
    for key in (
        "'turn_id', v_messages->'turn_id'",
        "'input_message_id', v_messages->'input_message_id'",
        "'output_message_id', v_messages->'output_message_id'",
        "'task_ids', v_tasks_result->'task_ids'",
    ):
        assert key in function


def test_attach_and_fail_only_transition_preparing_tasks() -> None:
    sql = _read(MIGRATION)
    attach = _function(
        sql, "attach_generation_external_task", "fail_prepared_generation_task"
    )
    fail = _function(sql, "fail_prepared_generation_task")

    assert "v_task.status <> 'preparing'" in attach
    assert "status = 'pending'" in attach
    assert "GENERATION_ATTACH_CONFLICT" in attach
    assert "FROM credit_transactions" in attach
    assert "v_credit.task_id IS DISTINCT FROM p_task_id" in attach
    assert "model_id = COALESCE(NULLIF(p_actual_model_id, ''), model_id)" in attach
    assert "request_params = COALESCE(p_actual_request_params, request_params)" in attach
    assert "v_task.status <> 'preparing'" in fail
    assert "status = 'failed'" in fail
    assert "completed_at = NOW()" in fail


def test_rpcs_are_not_public_and_rollback_is_guarded() -> None:
    sql = _read(MIGRATION)
    rollback = _read(ROLLBACK)

    for signature in (
        "_prepare_generation_messages(\n    TEXT, UUID, UUID, UUID, JSONB, JSONB\n)",
        "_prepare_generation_tasks(\n    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID\n)",
        "attach_generation_external_task(\n    UUID, TEXT, UUID, UUID, TEXT, JSONB\n)",
        "fail_prepared_generation_task(UUID, TEXT, TEXT, UUID)",
    ):
        assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC" in sql
    assert "REVOKE ALL ON FUNCTION prepare_generation(" in sql
    attach = _function(
        sql, "attach_generation_external_task", "fail_prepared_generation_task"
    )
    fail = _function(sql, "fail_prepared_generation_task")
    assert "v_task.org_id IS DISTINCT FROM p_org_id" in attach
    assert "v_task.org_id IS DISTINCT FROM p_org_id" in fail
    assert "ROLLBACK_148_PREPARING_TASKS_EXIST" in rollback
    assert "ROLLBACK_148_MULTIPLE_CREDIT_ATTEMPTS_EXIST" in rollback
    assert rollback.index("DROP FUNCTION IF EXISTS prepare_generation") < rollback.index(
        "DROP CONSTRAINT IF EXISTS tasks_status_check"
    )
