"""Bounded worker loop for Runtime-owned Scheduled WeCom delivery."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from uuid import UUID, uuid4

from loguru import logger

from services.agent.runtime.application.scheduled_wecom_router import (
    ScheduledWecomRouter,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    ScheduledWecomDeliveryRepositoryPort,
)
from services.agent.runtime.ports.scheduled_wecom_router import (
    ScheduledWecomRouteOutcome,
    ScheduledWecomRouteResult,
)


RequestIdFactory = Callable[[], str]


def _new_request_id() -> str:
    return str(uuid4())


class ScheduledRuntimeWecomWorker:
    """Run one recovery-first Scheduled WeCom pass at a time."""

    def __init__(
        self,
        repository: ScheduledWecomDeliveryRepositoryPort,
        router: ScheduledWecomRouter,
        *,
        worker_id: str,
        poll_interval_seconds: float = 2,
        lease_seconds: int = 60,
        request_id_factory: RequestIdFactory = _new_request_id,
    ) -> None:
        if not _valid_worker_id(worker_id):
            raise ValueError("invalid scheduled runtime worker_id")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds <= 0
        ):
            raise ValueError("invalid scheduled runtime poll interval")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 5 <= lease_seconds <= 900
        ):
            raise ValueError("invalid scheduled runtime lease")
        if not callable(request_id_factory):
            raise ValueError("invalid scheduled runtime request id factory")
        self._repository = repository
        self._router = router
        self._worker_id = worker_id
        self._poll_interval = float(poll_interval_seconds)
        self._lease_seconds = lease_seconds
        self._request_id_factory = request_id_factory
        self._running = False
        self._wake_event = asyncio.Event()
        self._pass_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("ScheduledRuntimeWecomWorker started")
        try:
            while self._running:
                processed = await self._run_safely()
                if processed:
                    continue
                await self._wait_for_next_poll()
        finally:
            self._running = False
            logger.info("ScheduledRuntimeWecomWorker stopped")

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()

    async def run_once(self) -> bool:
        """Run started recovery, prepared recovery, then fresh dispatch."""
        async with self._pass_lock:
            return await self._run_priority_pass()

    async def _run_priority_pass(self) -> bool:
        request_ids: set[str] = set()
        started = await self._repository.recover_started(
            request_id=self._next_request_id(request_ids),
            worker_id=self._worker_id,
        )
        if started is not None:
            return True

        prepared = await self._router.recover_prepared_once(
            request_id=self._next_request_id(request_ids),
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
        )
        if prepared.outcome is not ScheduledWecomRouteOutcome.EMPTY:
            return _route_was_processed(prepared)

        dispatched = await self._router.dispatch_once(
            request_id=self._next_request_id(request_ids),
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
        )
        return _route_was_processed(dispatched)

    async def _run_safely(self) -> bool:
        try:
            return await self.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "scheduled_runtime_wecom_pass_failed | "
                f"error={type(error).__name__}"
            )
            return False

    async def _wait_for_next_poll(self) -> None:
        self._wake_event.clear()
        if not self._running:
            return
        try:
            await asyncio.wait_for(
                self._wake_event.wait(), timeout=self._poll_interval,
            )
        except asyncio.TimeoutError:
            pass

    def _next_request_id(self, previous: set[str]) -> str:
        request_id = self._request_id_factory()
        if not _valid_request_id(request_id) or request_id in previous:
            raise RuntimeError("SCHEDULED_RUNTIME_WECOM_REQUEST_ID_INVALID")
        previous.add(request_id)
        return request_id


def _route_was_processed(result: ScheduledWecomRouteResult) -> bool:
    if result.outcome is ScheduledWecomRouteOutcome.EMPTY:
        return False
    if result.outcome in {
        ScheduledWecomRouteOutcome.UNAVAILABLE,
        ScheduledWecomRouteOutcome.CONFIG_UNAVAILABLE,
    }:
        return result.intent_id is not None or result.item_id is not None
    return True


def _valid_worker_id(worker_id: object) -> bool:
    return (
        isinstance(worker_id, str)
        and bool(worker_id)
        and worker_id == worker_id.strip()
        and len(worker_id) <= 128
    )


def _valid_request_id(request_id: object) -> bool:
    if not isinstance(request_id, str):
        return False
    try:
        return str(UUID(request_id)) == request_id
    except (ValueError, AttributeError):
        return False
