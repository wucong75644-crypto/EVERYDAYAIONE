"""Worker 活跃企业枚举及两个调用方的定向测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.db_scope import SET_DATABASE_SCOPE_SQL
from core.local_db import LocalDBClient
from services.background_task_worker import BackgroundTaskWorker


def _worker(db: MagicMock | None = None) -> BackgroundTaskWorker:
    with patch("services.background_task_worker.get_settings") as settings:
        settings.return_value = MagicMock(
            callback_base_url=None,
            poll_interval_seconds=0,
        )
        return BackgroundTaskWorker(db or MagicMock())


def _raw_local_db(
    payload: object = None,
    *,
    rpc_error: Exception | None = None,
) -> tuple[LocalDBClient, MagicMock]:
    pool = MagicMock()
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    pool.connection.return_value = connection_context
    transaction = MagicMock()
    connection.transaction.return_value = transaction
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection.cursor.return_value = cursor_context
    cursor.description = [("worker_list_active_organization_ids",)]
    cursor.fetchall.return_value = [
        {"worker_list_active_organization_ids": payload},
    ]
    if rpc_error is not None:
        cursor.execute.side_effect = [None, rpc_error]
    db = object.__new__(LocalDBClient)
    db._pool = pool
    return db, cursor


@pytest.mark.asyncio
async def test_raw_worker_db_sets_scope_before_enumeration_rpc() -> None:
    db, cursor = _raw_local_db({
        "outcome": "listed",
        "organization_ids": ["org-1", "org-2"],
    })
    worker = _worker(db)

    assert await worker._get_active_org_ids() == ["org-1", "org-2"]
    assert cursor.execute.call_args_list[0].args == (
        SET_DATABASE_SCOPE_SQL,
        ("", "", "worker", "worker-active-organizations"),
    )
    assert cursor.execute.call_args_list[1].args == (
        'SELECT "worker_list_active_organization_ids"()',
        [],
    )


@pytest.mark.asyncio
async def test_empty_rpc_result_is_successful_enumeration() -> None:
    db, _ = _raw_local_db({
        "outcome": "listed",
        "organization_ids": [],
    })

    assert await _worker(db)._get_active_org_ids() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"outcome": "failed", "organization_ids": []},
        {"outcome": "listed"},
        {"outcome": "listed", "organization_ids": None},
        {"outcome": "listed", "organization_ids": [None]},
    ],
)
async def test_invalid_rpc_response_fails_closed(payload: object) -> None:
    db, _ = _raw_local_db(payload)

    with pytest.raises(
        RuntimeError,
        match="WORKER_ACTIVE_ORGANIZATION_RESPONSE_INVALID",
    ):
        await _worker(db)._get_active_org_ids()


@pytest.mark.asyncio
async def test_rpc_failure_is_not_converted_to_empty_result() -> None:
    db, _ = _raw_local_db(
        rpc_error=RuntimeError("permission denied"),
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        await _worker(db)._get_active_org_ids()


@pytest.mark.asyncio
async def test_consistency_checks_orgs_and_individual_scope() -> None:
    worker = _worker()
    worker._get_active_org_ids = AsyncMock(
        return_value=["org-1", "org-2"],
    )
    checker = MagicMock()
    checker.check_and_alert = AsyncMock(
        return_value={"total_checked": 1, "total_issues": 0},
    )

    with patch(
        "services.data_consistency_checker.DataConsistencyChecker",
        return_value=checker,
    ) as checker_type:
        await worker.check_data_consistency()

    assert [
        call.args[0].org_id for call in checker_type.call_args_list
    ] == ["org-1", "org-2", None]
    assert checker.check_and_alert.await_count == 3
    assert worker._last_consistency_check is not None


@pytest.mark.asyncio
async def test_consistency_enumeration_failure_is_not_success() -> None:
    worker = _worker()
    worker._get_active_org_ids = AsyncMock(
        side_effect=RuntimeError("permission denied"),
    )

    await worker.check_data_consistency()

    assert worker._last_consistency_check is None


@pytest.mark.asyncio
async def test_model_scoring_runs_for_orgs_and_individual_scope() -> None:
    worker = _worker()

    with patch(
        "services.model_scorer.aggregate_model_scores",
        new_callable=AsyncMock,
        return_value=True,
    ) as aggregate, patch.object(
        worker,
        "_get_active_org_ids",
        new=AsyncMock(return_value=["org-1", "org-2"]),
    ), patch(
        "services.background_periodic_tasks.claim_periodic_job",
        new=AsyncMock(return_value=MagicMock(
            outcome="claimed",
            lease_token="token",
        )),
    ), patch(
        "services.background_periodic_tasks.finish_periodic_job",
        new=AsyncMock(),
    ):
        await worker._run_model_scoring()

    assert [
        call.kwargs["org_id"] for call in aggregate.await_args_list
    ] == ["org-1", "org-2", None]
    assert worker._last_scoring_aggregation is not None


@pytest.mark.asyncio
async def test_model_scoring_enumeration_failure_marks_lease_failed() -> None:
    worker = _worker()

    with patch.object(
        worker,
        "_get_active_org_ids",
        new=AsyncMock(side_effect=RuntimeError("permission denied")),
    ), patch(
        "services.background_periodic_tasks.claim_periodic_job",
        new=AsyncMock(return_value=MagicMock(
            outcome="claimed",
            lease_token="token",
        )),
    ), patch(
        "services.background_periodic_tasks.finish_periodic_job",
        new=AsyncMock(),
    ) as finish:
        await worker._run_model_scoring()

    assert finish.await_args.kwargs["succeeded"] is False
    assert worker._last_scoring_aggregation is None
