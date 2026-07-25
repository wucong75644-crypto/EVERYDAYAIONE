"""媒体 Worker 智能重试能力合同。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.worker_media_tasks import WorkerMediaTasks


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/173_worker_media_retry.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/173_worker_media_retry_rollback.sql"
).read_text()


def test_retry_migration_is_worker_only_and_table_grant_free() -> None:
    for name in (
        "worker_prepare_media_retry",
        "worker_abort_media_retry",
        "worker_commit_media_retry",
    ):
        assert f"CREATE FUNCTION {name}" in SQL
        assert f"DROP FUNCTION {name}" in ROLLBACK
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "atomic_refund_credits" in SQL
    assert "version = version + 1" in SQL
    assert "GRANT SELECT ON TABLE" not in SQL
    assert "GRANT UPDATE ON TABLE" not in SQL


def test_repository_prepare_and_commit_retry_contracts() -> None:
    db = MagicMock()
    execute = db.rpc.return_value.execute
    execute.return_value = SimpleNamespace(
        data={"outcome": "prepared", "transaction_id": "tx-1"},
    )
    repository = WorkerMediaTasks(db)

    assert repository.prepare_retry("old-1", 4, "new-model") == "tx-1"

    execute.return_value = SimpleNamespace(
        data={"outcome": "committed", "task": {"external_task_id": "new-1"}},
    )
    assert repository.commit_retry(
        "old-1", 4, "new-1", "new-model", {"_retry_count": 1}, "tx-1"
    ) == {"external_task_id": "new-1"}
