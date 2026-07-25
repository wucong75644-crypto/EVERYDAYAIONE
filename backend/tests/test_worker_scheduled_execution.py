"""定时任务 Worker 执行终态能力合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/177_worker_scheduled_execution.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/177_worker_scheduled_execution_rollback.sql"
).read_text()


def test_execution_capabilities_are_worker_only_and_atomic() -> None:
    for name in (
        "worker_create_scheduled_run",
        "worker_get_scheduled_task",
        "worker_complete_scheduled_run",
        "worker_fail_scheduled_run",
    ):
        assert f"CREATE FUNCTION {name}" in SQL
        assert f"DROP FUNCTION {name}" in ROLLBACK
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "FOR UPDATE OF run, task" in SQL
    assert "GRANT UPDATE ON TABLE" not in SQL
