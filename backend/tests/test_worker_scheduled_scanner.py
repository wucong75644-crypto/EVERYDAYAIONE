"""定时任务 Worker 扫描能力合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/176_worker_scheduled_scanner.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/176_worker_scheduled_scanner_rollback.sql"
).read_text()


def test_scanner_capabilities_are_worker_only() -> None:
    for name in (
        "worker_claim_due_scheduled_tasks",
        "worker_list_stale_scheduled_tasks",
        "worker_recover_stale_scheduled_task",
    ):
        assert f"CREATE FUNCTION {name}" in SQL
        assert f"DROP FUNCTION {name}" in ROLLBACK
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "FOR UPDATE SKIP LOCKED" in SQL
    assert "GRANT SELECT ON TABLE" not in SQL
