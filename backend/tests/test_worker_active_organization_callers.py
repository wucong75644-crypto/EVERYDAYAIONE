"""Worker 活跃企业枚举及两个调用方的定向测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.background_task_worker import BackgroundTaskWorker


def _worker(db: MagicMock | None = None) -> BackgroundTaskWorker:
    with patch("services.background_task_worker.get_settings") as settings:
        settings.return_value = MagicMock(
            callback_base_url=None,
            poll_interval_seconds=0,
        )
        return BackgroundTaskWorker(db or MagicMock())


@pytest.mark.asyncio
async def test_enumeration_returns_ids_from_closed_rpc_response() -> None:
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "outcome": "listed",
        "organization_ids": ["org-1", "org-2"],
    }
    worker = _worker(db)

    assert await worker._get_active_org_ids() == ["org-1", "org-2"]
    db.rpc.assert_called_once_with("worker_list_active_organization_ids")


@pytest.mark.asyncio
async def test_empty_rpc_result_is_successful_enumeration() -> None:
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "outcome": "listed",
        "organization_ids": [],
    }

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
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = payload

    with pytest.raises(
        RuntimeError,
        match="WORKER_ACTIVE_ORGANIZATION_RESPONSE_INVALID",
    ):
        await _worker(db)._get_active_org_ids()


@pytest.mark.asyncio
async def test_rpc_failure_is_not_converted_to_empty_result() -> None:
    db = MagicMock()
    db.rpc.side_effect = RuntimeError("permission denied")

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
