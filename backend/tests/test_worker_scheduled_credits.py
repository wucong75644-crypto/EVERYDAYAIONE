"""定时任务 Worker 积分能力合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/178_worker_scheduled_credits.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/178_worker_scheduled_credits_rollback.sql"
).read_text()


def test_scheduled_credit_capabilities_are_task_scoped() -> None:
    assert "CREATE FUNCTION worker_lock_scheduled_credits" in SQL
    assert "CREATE FUNCTION worker_settle_scheduled_credits" in SQL
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "run.org_id = task.org_id" in SQL
    assert "partial_refund_credits" in SQL
    assert "atomic_refund_credits" in SQL
    assert "GRANT UPDATE ON TABLE" not in SQL
    assert "DROP FUNCTION worker_lock_scheduled_credits" in ROLLBACK
