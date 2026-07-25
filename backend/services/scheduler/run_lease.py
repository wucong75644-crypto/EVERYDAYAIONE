"""定时任务执行租约协调。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from services.scheduler.worker_store import (
    ScheduledRunLease,
    ScheduledWorkerStore,
)


class ScheduledRunLeaseLost(RuntimeError):
    pass


async def execute_with_scheduled_lease(
    store: ScheduledWorkerStore,
    task_id: str,
    run: ScheduledRunLease,
    execution: Awaitable[Any],
    *,
    renew_interval_seconds: float = 30,
) -> Any:
    """执行 Agent，并在租约丢失时取消仍在运行的执行。"""
    execution_task = asyncio.create_task(execution)
    renew_task = asyncio.create_task(
        _renew_until_cancelled(
            store,
            task_id,
            run,
            renew_interval_seconds,
        )
    )
    done, _pending = await asyncio.wait(
        {execution_task, renew_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if renew_task in done:
        execution_task.cancel()
        await asyncio.gather(execution_task, return_exceptions=True)
        return await renew_task
    renew_task.cancel()
    await asyncio.gather(renew_task, return_exceptions=True)
    return await execution_task


async def _renew_until_cancelled(
    store: ScheduledWorkerStore,
    task_id: str,
    run: ScheduledRunLease,
    interval: float,
) -> None:
    while True:
        await asyncio.sleep(interval)
        if not store.renew(task_id, run):
            raise ScheduledRunLeaseLost("SCHEDULED_RUN_LEASE_LOST")
