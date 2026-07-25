"""媒体 Worker 控制面迁移与应用边界合同。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.worker_media_tasks import WorkerMediaTasks


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/171_worker_media_task_control.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback"
    / "171_worker_media_task_control_rollback.sql"
).read_text()


def test_migration_is_worker_only_and_never_grants_tasks_table() -> None:
    for name in (
        "worker_discover_media_tasks",
        "worker_get_media_task",
        "worker_touch_media_task",
        "worker_claim_media_task_completion",
        "worker_settle_media_batch_item",
        "worker_discover_legacy_active_tasks",
        "worker_fail_legacy_stale_task",
        "worker_get_media_batch_message",
        "worker_commit_media_batch_message",
    ):
        assert f"CREATE FUNCTION {name}" in SQL
        assert f"DROP FUNCTION {name}" in ROLLBACK
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "TO everydayai_worker;" in SQL
    assert "GRANT SELECT ON TABLE" not in SQL
    assert "GRANT UPDATE ON TABLE" not in SQL


def test_claim_is_versioned_and_media_only() -> None:
    assert "version = p_expected_version" in SQL
    assert "version = version + 1" in SQL
    assert "type IN ('image', 'video')" in SQL
    assert "status IN ('pending', 'running')" in SQL


def test_repository_maps_claimed_task_and_rejects_other_outcomes() -> None:
    db = MagicMock()
    rpc = db.rpc.return_value.execute
    rpc.return_value = SimpleNamespace(
        data={"outcome": "claimed", "task": {"id": "task-1"}},
    )
    repository = WorkerMediaTasks(db)

    assert repository.claim_completion("external-1", 3) == {"id": "task-1"}
    db.rpc.assert_called_with(
        "worker_claim_media_task_completion",
        {
            "p_external_task_id": "external-1",
            "p_expected_version": 3,
        },
    )

    rpc.return_value = SimpleNamespace(data={"outcome": "not_claimed"})
    assert repository.claim_completion("external-1", 3) is None


def test_repository_maps_settled_batch_snapshot() -> None:
    db = MagicMock()
    rpc = db.rpc.return_value.execute
    rpc.return_value = SimpleNamespace(
        data={"outcome": "settled", "batch_tasks": [{"id": "task-1"}]},
    )
    repository = WorkerMediaTasks(db)

    assert repository.settle_batch_item(
        "external-1", 4, "completed", {"url": "https://asset"}
    ) == [{"id": "task-1"}]
    db.rpc.assert_called_with(
        "worker_settle_media_batch_item",
        {
            "p_external_task_id": "external-1",
            "p_expected_version": 4,
            "p_status": "completed",
            "p_result_data": {"url": "https://asset"},
            "p_error_message": None,
        },
    )
