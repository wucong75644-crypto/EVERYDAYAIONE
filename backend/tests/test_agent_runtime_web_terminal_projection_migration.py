from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = (
    ROOT / "migrations/228_08n_agent_runtime_web_terminal_projection.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_08n_agent_runtime_web_terminal_projection_rollback.sql"
).read_text()


def test_terminal_projection_closes_bound_assistant_placeholder() -> None:
    assert "v_status IN ('failed','cancelled')" in SQL
    assert "t.assistant_message_id IS DISTINCT FROM m.id" in SQL
    assert "m.reply_to_message_id IS DISTINCT FROM" in SQL
    assert "UPDATE messages SET status='failed'" in SQL
    assert "projected_message_id:=m.id" in SQL


def test_repair_is_narrow_admin_only_idempotent_and_audited() -> None:
    assert "session_user<>'everydayai_runtime_admin'" in SQL
    assert "NOT tenant_platform_admin()" in SQL
    assert "p_repair_request_id" in SQL
    assert "runtime_terminal_projection_repair_request_id" in SQL
    assert "'outcome','already_repaired'" in SQL
    assert "TO everydayai_runtime_admin" in SQL
    assert "TO everydayai_agent_runtime_worker" not in SQL


def test_rollback_removes_repair_and_restores_task_only_projection() -> None:
    assert "DROP FUNCTION repair_agent_runtime_web_terminal_projection_v1" in ROLLBACK
    assert "UPDATE messages SET status='failed'" not in ROLLBACK
    assert "UPDATE tasks SET status=v_status" in ROLLBACK
