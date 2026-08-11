"""Scheduled Runtime WeCom worker priority and polling contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest

from services.agent.runtime.ports.scheduled_wecom_router import (
    ScheduledWecomRouteOutcome,
    ScheduledWecomRouteResult,
)
from services.wecom.scheduled_runtime_worker import ScheduledRuntimeWecomWorker


class _Repository:
    def __init__(self, results: list[object] | None = None) -> None:
        self.results = list(results or [None])
        self.calls: list[tuple[str, str]] = []

    async def recover_started(self, *, request_id: str, worker_id: str) -> object:
        self.calls.append((request_id, worker_id))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Router:
    def __init__(
        self,
        prepared: list[ScheduledWecomRouteResult | BaseException] | None = None,
        dispatched: list[ScheduledWecomRouteResult | BaseException] | None = None,
    ) -> None:
        self.prepared = list(prepared or [_result(ScheduledWecomRouteOutcome.EMPTY)])
        self.dispatched = list(dispatched or [_result(ScheduledWecomRouteOutcome.EMPTY)])
        self.calls: list[tuple[str, str, str, int]] = []

    async def recover_prepared_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        self.calls.append(("prepared", request_id, worker_id, lease_seconds))
        result = self.prepared.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def dispatch_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        self.calls.append(("dispatch", request_id, worker_id, lease_seconds))
        result = self.dispatched.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Reconciler:
    def __init__(self, results: list[object] | None = None) -> None:
        self.results = list(results or [None])

    async def reconcile_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> object:
        del request_id, worker_id, lease_seconds
        if not self.results:
            return None
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _ConcurrentRepository:
    def __init__(self) -> None:
        self.active_passes = 0
        self.max_active_passes = 0

    async def recover_started(self, *, request_id: str, worker_id: str) -> None:
        del request_id, worker_id
        self.active_passes += 1
        self.max_active_passes = max(self.max_active_passes, self.active_passes)
        await asyncio.sleep(0)
        self.active_passes -= 1


class _ConcurrentRouter:
    def __init__(self) -> None:
        self.active_dispatch = 0
        self.max_active_dispatch = 0

    async def recover_prepared_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        del request_id, worker_id, lease_seconds
        await asyncio.sleep(0)
        return _result(ScheduledWecomRouteOutcome.EMPTY)

    async def dispatch_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        del request_id, worker_id, lease_seconds
        self.active_dispatch += 1
        self.max_active_dispatch = max(
            self.max_active_dispatch, self.active_dispatch,
        )
        await asyncio.sleep(0)
        self.active_dispatch -= 1
        return _result(ScheduledWecomRouteOutcome.ACCEPTED, identified=True)


class _LifecycleRouter:
    def __init__(self) -> None:
        self.dispatch_entered = [asyncio.Event(), asyncio.Event()]
        self.dispatch_release = [asyncio.Event(), asyncio.Event()]
        self.dispatch_calls = 0
        self.active_dispatch = 0
        self.max_active_dispatch = 0

    async def recover_prepared_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        del request_id, worker_id, lease_seconds
        return _result(ScheduledWecomRouteOutcome.EMPTY)

    async def dispatch_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        del request_id, worker_id, lease_seconds
        index = self.dispatch_calls
        self.dispatch_calls += 1
        self.active_dispatch += 1
        self.max_active_dispatch = max(
            self.max_active_dispatch, self.active_dispatch,
        )
        self.dispatch_entered[index].set()
        try:
            await self.dispatch_release[index].wait()
        finally:
            self.active_dispatch -= 1
        return _result(ScheduledWecomRouteOutcome.ACCEPTED, identified=True)


class _TrackedWorker(ScheduledRuntimeWecomWorker):
    def __init__(self, repository: object, router: object) -> None:
        super().__init__(
            repository, _Reconciler(), router, worker_id="lifecycle-worker",
            poll_interval_seconds=60,
        )
        self.active_passes = 0
        self.max_active_passes = 0

    async def _run_priority_pass(self) -> bool:
        self.active_passes += 1
        self.max_active_passes = max(
            self.max_active_passes, self.active_passes,
        )
        try:
            return await super()._run_priority_pass()
        finally:
            self.active_passes -= 1


class _SelfStoppingRepository:
    worker: ScheduledRuntimeWecomWorker | None = None

    async def recover_started(self, *, request_id: str, worker_id: str) -> object:
        del request_id, worker_id
        assert self.worker is not None
        await self.worker.stop()
        return object()


def _result(
    outcome: ScheduledWecomRouteOutcome,
    *,
    identified: bool = False,
) -> ScheduledWecomRouteResult:
    return ScheduledWecomRouteResult(
        outcome=outcome,
        intent_id="intent" if identified else None,
        item_id="item" if identified else None,
    )


def _ids() -> Iterator[str]:
    while True:
        yield str(uuid4())


def _worker(
    repository: _Repository,
    router: _Router,
    *,
    request_ids: Iterator[str] | None = None,
    poll_interval_seconds: float = 2,
) -> ScheduledRuntimeWecomWorker:
    values = request_ids or _ids()
    return ScheduledRuntimeWecomWorker(
        repository, _Reconciler(), router, worker_id="scheduled-runtime-worker",
        poll_interval_seconds=poll_interval_seconds, lease_seconds=60,
        request_id_factory=lambda: next(values),
    )


@pytest.mark.parametrize("worker_id", ("", " ", " worker", "worker ", "w" * 129))
def test_rejects_noncanonical_worker_id(worker_id: str) -> None:
    with pytest.raises(ValueError, match="worker_id"):
        ScheduledRuntimeWecomWorker(
            _Repository(), _Reconciler(), _Router(), worker_id=worker_id,
        )


@pytest.mark.parametrize("poll", (0, -1, float("inf"), float("nan"), True))
def test_rejects_invalid_poll_interval(poll: float) -> None:
    with pytest.raises(ValueError, match="poll interval"):
        ScheduledRuntimeWecomWorker(
            _Repository(), _Reconciler(), _Router(), worker_id="worker",
            poll_interval_seconds=poll,
        )


@pytest.mark.parametrize("lease", (4, 901, True, 60.0))
def test_rejects_invalid_lease(lease: int) -> None:
    with pytest.raises(ValueError, match="lease"):
        ScheduledRuntimeWecomWorker(
            _Repository(), _Reconciler(), _Router(), worker_id="worker",
            lease_seconds=lease,
        )


@pytest.mark.asyncio
async def test_started_recovery_has_absolute_priority() -> None:
    repository = _Repository([object()])
    reconciler = _Reconciler()
    router = _Router()

    worker = ScheduledRuntimeWecomWorker(
        repository, reconciler, router, worker_id="scheduled-runtime-worker",
    )
    assert await worker.run_once() is True
    assert len(repository.calls) == 1
    assert reconciler.results == [None]
    assert router.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    (
        ScheduledWecomRouteOutcome.ACCEPTED,
        ScheduledWecomRouteOutcome.REJECTED,
        ScheduledWecomRouteOutcome.UNKNOWN,
        ScheduledWecomRouteOutcome.CONFIG_UNAVAILABLE,
        ScheduledWecomRouteOutcome.UNAVAILABLE,
    ),
)
async def test_identified_prepared_result_blocks_normal_dispatch(
    outcome: ScheduledWecomRouteOutcome,
) -> None:
    router = _Router(prepared=[_result(outcome, identified=True)])

    assert await _worker(_Repository(), router).run_once() is True
    assert [call[0] for call in router.calls] == ["prepared"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ("started", "prepared", "dispatch"))
async def test_stage_exception_stops_lower_priority_work(stage: str) -> None:
    error = RuntimeError(f"{stage}-failed")
    repository = _Repository([error] if stage == "started" else [None])
    router = _Router(
        prepared=[error] if stage == "prepared" else None,
        dispatched=[error] if stage == "dispatch" else None,
    )

    with pytest.raises(RuntimeError, match=f"{stage}-failed"):
        await ScheduledRuntimeWecomWorker(
            repository, _Reconciler(), router,
            worker_id="scheduled-runtime-worker",
        ).run_once()

    expected = {
        "started": [],
        "prepared": ["prepared"],
        "dispatch": ["prepared", "dispatch"],
    }
    assert [call[0] for call in router.calls] == expected[stage]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ("started", "prepared", "dispatch"))
async def test_cancelled_error_propagates_without_fallback(stage: str) -> None:
    cancelled = asyncio.CancelledError()
    repository = _Repository([cancelled] if stage == "started" else [None])
    router = _Router(
        prepared=[cancelled] if stage == "prepared" else None,
        dispatched=[cancelled] if stage == "dispatch" else None,
    )

    with pytest.raises(asyncio.CancelledError):
        await ScheduledRuntimeWecomWorker(
            repository, _Reconciler(), router,
            worker_id="scheduled-runtime-worker",
        ).run_once()

    expected = {
        "started": [],
        "prepared": ["prepared"],
        "dispatch": ["prepared", "dispatch"],
    }
    assert [call[0] for call in router.calls] == expected[stage]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ("prepared", "dispatch"))
async def test_unidentified_unavailable_is_unprocessed(stage: str) -> None:
    unavailable = _result(ScheduledWecomRouteOutcome.UNAVAILABLE)
    router = _Router(
        prepared=[unavailable] if stage == "prepared" else None,
        dispatched=[unavailable] if stage == "dispatch" else None,
    )

    assert await _worker(_Repository(), router).run_once() is False
    expected = ["prepared"] if stage == "prepared" else ["prepared", "dispatch"]
    assert [call[0] for call in router.calls] == expected


@pytest.mark.asyncio
async def test_unidentified_unavailable_waits_instead_of_busy_spinning() -> None:
    repository = _Repository([None, None])
    router = _Router(prepared=[
        _result(ScheduledWecomRouteOutcome.UNAVAILABLE),
        _result(ScheduledWecomRouteOutcome.UNAVAILABLE),
    ])
    worker = _worker(repository, router, poll_interval_seconds=60)

    task = asyncio.create_task(worker.start())
    for _ in range(20):
        if router.calls:
            break
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(router.calls) == 1
    await worker.stop()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_start_stop_is_idempotent_and_wakes_poll() -> None:
    repository = _Repository([None])
    router = _Router(prepared=[_result(ScheduledWecomRouteOutcome.UNAVAILABLE)])
    worker = _worker(repository, router, poll_interval_seconds=60)

    task = asyncio.create_task(worker.start())
    for _ in range(20):
        if router.calls:
            break
        await asyncio.sleep(0)
    second_start = asyncio.create_task(worker.start())
    await second_start
    await worker.stop()
    await worker.stop()
    await asyncio.wait_for(task, timeout=1)

    assert len(repository.calls) == 1


@pytest.mark.asyncio
async def test_stop_during_pass_fences_concurrent_restart_to_one_loop() -> None:
    repository = _Repository([None, None])
    router = _LifecycleRouter()
    worker = _TrackedWorker(repository, router)

    old_loop = asyncio.create_task(worker.start())
    await asyncio.wait_for(router.dispatch_entered[0].wait(), timeout=1)
    stopping = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    restarted = asyncio.create_task(worker.start())
    await asyncio.sleep(0)

    assert not stopping.done()
    assert not restarted.done()
    assert router.dispatch_calls == 1

    router.dispatch_release[0].set()
    await asyncio.wait_for(router.dispatch_entered[1].wait(), timeout=1)

    assert old_loop.done()
    assert stopping.done()
    assert not restarted.done()
    assert worker._loop_task is restarted
    assert worker.max_active_passes == 1
    assert router.max_active_dispatch == 1

    final_stop = asyncio.create_task(worker.stop())
    router.dispatch_release[1].set()
    await asyncio.wait_for(final_stop, timeout=1)
    await asyncio.wait_for(restarted, timeout=1)
    assert worker._loop_task is None


@pytest.mark.asyncio
async def test_cancelled_stop_waiter_does_not_cancel_owned_loop() -> None:
    repository = _Repository([None])
    router = _LifecycleRouter()
    worker = _TrackedWorker(repository, router)
    loop = asyncio.create_task(worker.start())
    await asyncio.wait_for(router.dispatch_entered[0].wait(), timeout=1)

    stopping = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert not loop.done()

    router.dispatch_release[0].set()
    await asyncio.wait_for(loop, timeout=1)
    assert worker._loop_task is None


@pytest.mark.asyncio
async def test_loop_cancellation_propagates_and_releases_ownership() -> None:
    repository = _Repository([None])
    router = _LifecycleRouter()
    worker = _TrackedWorker(repository, router)
    loop = asyncio.create_task(worker.start())
    await asyncio.wait_for(router.dispatch_entered[0].wait(), timeout=1)

    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop

    assert worker._loop_task is None
    assert worker.active_passes == 0
    assert router.active_dispatch == 0


@pytest.mark.asyncio
async def test_stop_from_owned_loop_does_not_self_wait() -> None:
    repository = _SelfStoppingRepository()
    worker = ScheduledRuntimeWecomWorker(
        repository, _Reconciler(), _Router(), worker_id="self-stopping-worker",
    )
    repository.worker = worker

    await asyncio.wait_for(worker.start(), timeout=1)

    assert worker._loop_task is None


@pytest.mark.asyncio
async def test_fifty_concurrent_calls_serialize_pass_and_dispatch() -> None:
    repository = _ConcurrentRepository()
    router = _ConcurrentRouter()
    worker = _worker(repository, router)

    results = await asyncio.gather(*(worker.run_once() for _ in range(50)))

    assert all(results)
    assert repository.max_active_passes == 1
    assert router.max_active_dispatch == 1


@pytest.mark.asyncio
async def test_invalid_generated_request_stops_before_repository_call() -> None:
    repository = _Repository()
    worker = _worker(repository, _Router(), request_ids=iter(["not-a-uuid"]))

    with pytest.raises(RuntimeError, match="REQUEST_ID_INVALID"):
        await worker.run_once()
    assert repository.calls == []


@pytest.mark.asyncio
async def test_request_id_cannot_be_reused_between_stages() -> None:
    request_id = str(uuid4())
    repository = _Repository()
    worker = _worker(
        repository, _Router(), request_ids=iter([request_id, request_id]),
    )

    with pytest.raises(RuntimeError, match="REQUEST_ID_INVALID"):
        await worker.run_once()
    assert repository.calls == [(request_id, "scheduled-runtime-worker")]


@pytest.mark.asyncio
async def test_default_factory_emits_canonical_uuid4() -> None:
    repository = _Repository([object()])
    worker = ScheduledRuntimeWecomWorker(
        repository, _Reconciler(), _Router(), worker_id="worker",
    )

    assert await worker.run_once() is True
    parsed = UUID(repository.calls[0][0])
    assert parsed.version == 4 and str(parsed) == repository.calls[0][0]
