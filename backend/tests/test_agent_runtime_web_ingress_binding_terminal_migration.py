from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = (
    ROOT / "migrations/228_08k_agent_runtime_web_ingress_binding_terminal.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_08k_agent_runtime_web_ingress_binding_terminal_rollback.sql"
).read_text()


def test_web_runtime_binding_uses_current_input_not_legacy_context_anchor() -> None:
    assert "_agent_runtime_validate_web_task_binding" in SQL
    assert "p_through_message_id IS DISTINCT FROM p_input_message_id" in SQL
    assert "task.context_through_message_id" not in SQL
    assert "source = 'web'" in SQL
    assert "reply_to_message_id = p_input_message_id" in SQL
    assert "p_channel <> 'web'" in SQL
    assert "tenant_actor_user_id() IS DISTINCT FROM p_user_id" in SQL
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in SQL
    assert "p_payload->>'task_id' IS DISTINCT FROM p_task_id::TEXT" in SQL


def test_ingress_failure_atomically_closes_task_and_placeholder() -> None:
    assert "CREATE FUNCTION fail_web_runtime_ingress_task" in SQL
    assert "task.status NOT IN ('pending','preparing')" in SQL
    assert "runtime_rejected" in SQL
    assert "UPDATE tasks SET status='failed'" in SQL
    assert "UPDATE messages SET status='failed',is_error=TRUE" in SQL
    assert "TO everydayai_runtime" in SQL


def test_rollback_removes_new_capabilities_and_restores_v6() -> None:
    assert "DROP FUNCTION fail_web_runtime_ingress_task" in ROLLBACK
    assert "DROP FUNCTION _agent_runtime_validate_web_task_binding" in ROLLBACK
    assert "mark_prepared_task_runtime_owned" in ROLLBACK
    assert "runtime_pending" in ROLLBACK
