"""Four-stage Scheduled WeCom worker priority contracts."""

from __future__ import annotations

from uuid import UUID

import pytest

from services.agent.runtime.ports.scheduled_wecom_router import (
    ScheduledWecomRouteOutcome,
    ScheduledWecomRouteResult,
)
from services.wecom.scheduled_runtime_worker import ScheduledRuntimeWecomWorker


class _Repository:
    def __init__(self, events: list[str], result: object = None) -> None:
        self.events = events
        self.result = result
        self.request_ids: list[str] = []

    async def recover_started(self, *, request_id: str, worker_id: str) -> object:
        assert worker_id == "worker"
        self.events.append("started")
        self.request_ids.append(request_id)
        return self.result


class _Reconciler:
    def __init__(self, events: list[str], result: object = None) -> None:
        self.events = events
        self.result = result
        self.request_ids: list[str] = []

    async def reconcile_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> object:
        assert (worker_id, lease_seconds) == ("worker", 60)
        self.events.append("reconcile")
        self.request_ids.append(request_id)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _Router:
    def __init__(
        self,
        events: list[str],
        prepared: ScheduledWecomRouteOutcome = ScheduledWecomRouteOutcome.EMPTY,
        dispatched: ScheduledWecomRouteOutcome = ScheduledWecomRouteOutcome.EMPTY,
    ) -> None:
        self.events = events
        self.prepared = prepared
        self.dispatched = dispatched
        self.request_ids: list[str] = []

    async def recover_prepared_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        assert (worker_id, lease_seconds) == ("worker", 60)
        self.events.append("prepared")
        self.request_ids.append(request_id)
        return _result(self.prepared)

    async def dispatch_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        assert (worker_id, lease_seconds) == ("worker", 60)
        self.events.append("dispatch")
        self.request_ids.append(request_id)
        return _result(self.dispatched)


def _result(outcome: ScheduledWecomRouteOutcome) -> ScheduledWecomRouteResult:
    identified = outcome is not ScheduledWecomRouteOutcome.EMPTY
    return ScheduledWecomRouteResult(
        outcome=outcome,
        intent_id="intent" if identified else None,
        item_id="item" if identified else None,
    )


@pytest.mark.asyncio
async def test_four_empty_stages_are_strict_and_use_independent_request_ids() -> None:
    events: list[str] = []
    repository = _Repository(events)
    reconciler = _Reconciler(events)
    router = _Router(events)
    worker = ScheduledRuntimeWecomWorker(
        repository, reconciler, router, worker_id="worker",
    )

    assert await worker.run_once() is False
    assert events == ["started", "reconcile", "prepared", "dispatch"]
    request_ids = (
        repository.request_ids + reconciler.request_ids + router.request_ids
    )
    assert len(request_ids) == len(set(request_ids)) == 4
    assert all(str(UUID(request_id)) == request_id for request_id in request_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("started", "reconciled", "prepared", "expected"),
    (
        (object(), None, ScheduledWecomRouteOutcome.EMPTY, ["started"]),
        (
            None, object(), ScheduledWecomRouteOutcome.EMPTY,
            ["started", "reconcile"],
        ),
        (
            None, None, ScheduledWecomRouteOutcome.ACCEPTED,
            ["started", "reconcile", "prepared"],
        ),
    ),
)
async def test_each_higher_stage_blocks_all_lower_stages(
    started: object,
    reconciled: object,
    prepared: ScheduledWecomRouteOutcome,
    expected: list[str],
) -> None:
    events: list[str] = []
    worker = ScheduledRuntimeWecomWorker(
        _Repository(events, started),
        _Reconciler(events, reconciled),
        _Router(events, prepared=prepared),
        worker_id="worker",
    )

    assert await worker.run_once() is True
    assert events == expected


@pytest.mark.asyncio
async def test_reconcile_exception_fails_closed_before_dispatch() -> None:
    events: list[str] = []
    worker = ScheduledRuntimeWecomWorker(
        _Repository(events),
        _Reconciler(events, RuntimeError("reconcile-failed")),
        _Router(events),
        worker_id="worker",
    )

    with pytest.raises(RuntimeError, match="reconcile-failed"):
        await worker.run_once()
    assert events == ["started", "reconcile"]
