from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = (
    ROOT / "migrations/228_08l_agent_runtime_web_partial_failure_repair.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_08l_agent_runtime_web_partial_failure_repair_rollback.sql"
).read_text()


def test_partial_startup_failure_can_close_active_placeholder() -> None:
    assert "task.terminal_reason = 'startup_recovery_failed'" in SQL
    assert "output_message.status::TEXT NOT IN" in SQL
    assert "owner_unresolved" in SQL
    assert "ELSE task.terminal_reason END" in SQL
    assert "UPDATE messages SET status='failed',is_error=TRUE" in SQL


def test_rollback_restores_pre_partial_repair_contract() -> None:
    assert "startup_recovery_failed" not in ROLLBACK
    assert "task.status NOT IN ('pending','preparing')" in ROLLBACK
    assert "terminal_reason='runtime_ingress_failed'" in ROLLBACK
