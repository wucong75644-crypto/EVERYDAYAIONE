"""Lifecycle loop for the dedicated Sandbox Job Worker process."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .worker import SandboxJobWorker


class SandboxJobWorkerService:
    """Poll durable execution, reconciliation, and cleanup work."""

    def __init__(
        self, worker: SandboxJobWorker, *,
        idle_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if idle_seconds <= 0:
            raise ValueError("SANDBOX_WORKER_IDLE_SECONDS_INVALID")
        self._worker = worker
        self._idle_seconds = idle_seconds
        self._sleep = sleep
        self._stopping = asyncio.Event()
        self._cycles_since_cleanup = 0

    def stop(self) -> None:
        self._worker.drain()
        self._stopping.set()

    async def run(self) -> None:
        while not self._stopping.is_set():
            execution = await self._worker.run_once()
            if self._stopping.is_set():
                break
            reconciliation = await self._worker.reconcile_next()
            self._cycles_since_cleanup += 1
            if self._cycles_since_cleanup >= 3600:
                self._worker.cleanup_expired_partials()
                self._cycles_since_cleanup = 0
            if not (
                execution.worked
                or reconciliation.worked
            ):
                await self._sleep(self._idle_seconds)
