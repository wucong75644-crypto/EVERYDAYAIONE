"""定时任务扫描器

嵌入 BackgroundTaskWorker.start() 主循环，每轮扫描到期任务并并发执行。

设计文档: docs/document/TECH_定时任务心跳系统.md §4.2
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any, List

from loguru import logger


class ScheduledTaskScanner:
    """定时任务扫描器（在 BackgroundTaskWorker 主循环中调用）

    每轮调用 poll() 时：
    1. 通过 claim_due_tasks RPC 原子领取到期任务（SKIP LOCKED 防并发）
    2. 用 Semaphore 控制并发数
    3. 调用 ScheduledTaskExecutor 执行
    """

    # 单轮最多领取的任务数（防止单轮执行时间过长）
    BATCH_SIZE = 5
    # 同时执行的最大任务数
    MAX_CONCURRENCY = 3

    def __init__(self, db: Any, executor: Any = None) -> None:
        self.db = db
        self._executor = executor  # ScheduledTaskExecutor 实例
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)

    async def poll(self) -> int:
        """扫描到期任务并 fire-and-forget 执行

        Returns:
            本轮领取到的任务数

        注意：使用 asyncio.create_task 后台执行，**不等待任务完成**。
        否则单轮 poll 会阻塞 BackgroundTaskWorker 主循环最多
        BATCH_SIZE * timeout = 5 * 180s = 15min，导致其他后台任务延迟。

        并发数仍受 self._semaphore 控制（实例级共享）。
        """
        if self._executor is None:
            # 延迟导入避免循环依赖
            from services.scheduler.task_executor import ScheduledTaskExecutor
            self._executor = ScheduledTaskExecutor(self.db)

        try:
            now = datetime.now(timezone.utc)
            tasks = await self._claim_due_tasks(now, self.BATCH_SIZE)
        except Exception as e:
            logger.error(f"ScheduledTaskScanner claim error | {e}")
            return 0

        if not tasks:
            return 0

        logger.info(f"ScheduledTaskScanner | claimed {len(tasks)} tasks (fire-and-forget)")

        # fire-and-forget：后台执行，不阻塞 worker 主循环
        for task in tasks:
            asyncio.create_task(self._run_with_limit(task))

        return len(tasks)

    async def _claim_due_tasks(self, now: datetime, limit: int) -> List[dict]:
        """通过 RPC 原子领取到期任务"""
        try:
            result = self.db.rpc("claim_due_tasks", {
                "p_now": now.isoformat(),
                "p_limit": limit,
            }).execute()
            return list(result.data or [])
        except Exception as e:
            logger.error(f"_claim_due_tasks error | {e}")
            return []

    async def _run_with_limit(self, task: dict) -> None:
        async with self._semaphore:
            try:
                await self._executor.execute(task)
            except Exception as e:
                logger.exception(
                    f"ScheduledTaskExecutor.execute crashed | "
                    f"task_id={task.get('id')} | error={e}"
                )
