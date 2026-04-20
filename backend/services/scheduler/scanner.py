"""定时任务扫描器

嵌入 BackgroundTaskWorker.start() 主循环，每轮扫描到期任务并并发执行。

设计文档: docs/document/TECH_定时任务心跳系统.md §4.2
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, List

from loguru import logger


# 任务卡在 running 超过此时间视为卡死（正常任务 timeout_sec 最大 600s）
_STALE_RUNNING_THRESHOLD = timedelta(minutes=15)


class ScheduledTaskScanner:
    """定时任务扫描器（在 BackgroundTaskWorker 主循环中调用）

    每轮调用 poll() 时：
    0. 恢复卡在 running 超时的任务（防进程崩溃导致任务永久卡死）
    1. 通过 claim_due_tasks RPC 原子领取到期任务（SKIP LOCKED 防并发）
    2. 用 Semaphore 控制并发数
    3. 调用 ScheduledTaskExecutor 执行
    """

    # 单轮最多领取的任务数（防止单轮执行时间过长）
    BATCH_SIZE = 5
    # 同时执行的最大任务数
    MAX_CONCURRENCY = 3
    # 恢复检查间隔（不必每轮都查，每 5 分钟一次足够）
    _RECOVER_INTERVAL = timedelta(minutes=5)

    def __init__(self, db: Any, executor: Any = None) -> None:
        self.db = db
        self._executor = executor  # ScheduledTaskExecutor 实例
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)
        self._last_recover_check: datetime | None = None

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

        # 0. 定期恢复卡死任务
        await self._recover_stale_running()

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
        """通过 SQL 函数原子领取到期任务

        必须使用 SELECT * FROM func() 而非 SELECT func()：
        后者返回复合类型的文本表示（字符串），前者展开为列（dict_row）。
        db.rpc() 内部用 SELECT func() 调用，对 RETURNS SETOF 不兼容。
        """
        try:
            with self.db.pool.connection() as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM claim_due_tasks(%s, %s)",
                        [now, int(limit)],
                    )
                    rows = cur.fetchall()
            # psycopg3 dict_row 返回 UUID 对象，统一转 str 防止下游
            # Path 拼接 / JSON 序列化失败
            from uuid import UUID as _UUID
            result = []
            for row in (rows or []):
                result.append({
                    k: str(v) if isinstance(v, _UUID) else v
                    for k, v in row.items()
                })
            if result:
                logger.info(
                    f"_claim_due_tasks | claimed={len(result)} | "
                    f"ids={[r.get('id') for r in result]}"
                )
            return result
        except Exception as e:
            logger.error(f"_claim_due_tasks error | {e}")
            return []

    async def _recover_stale_running(self) -> None:
        """恢复卡在 running 状态超时的任务

        场景：进程崩溃/重启导致 _on_success/_on_failure 未执行，
        任务永远停在 status='running' + next_run_at=NULL。

        恢复策略：
        - 卡超 15 分钟的 running 任务 → 改回 active + 按 cron 算 next_run_at
        - 同时把对应的 running 执行记录标记为 failed
        """
        now = datetime.now(timezone.utc)
        if (
            self._last_recover_check is not None
            and now - self._last_recover_check < self._RECOVER_INTERVAL
        ):
            return
        self._last_recover_check = now

        try:
            cutoff = (now - _STALE_RUNNING_THRESHOLD).isoformat()
            # 找卡死任务：status=running 且 last_run_at 早于阈值（或为空）
            result = (
                self.db.table("scheduled_tasks")
                .select("id, cron_expr, timezone, schedule_type")
                .eq("status", "running")
                .lt("updated_at", cutoff)
                .execute()
            )
            stale = list(result.data or [])
            if not stale:
                return

            from services.scheduler.cron_utils import calc_next_run

            for task in stale:
                task_id = task["id"]
                tz = task.get("timezone") or "Asia/Shanghai"
                cron_expr = task.get("cron_expr")

                # 单次任务卡死 → 直接暂停
                if task.get("schedule_type") == "once":
                    next_run = None
                    new_status = "paused"
                else:
                    next_run = calc_next_run(cron_expr, tz) if cron_expr else None
                    new_status = "active"

                self.db.table("scheduled_tasks").update({
                    "status": new_status,
                    "next_run_at": next_run.isoformat() if next_run else None,
                    "updated_at": now.isoformat(),
                }).eq("id", task_id).execute()

                # 把遗留的 running 执行记录标记为 failed
                self.db.table("scheduled_task_runs").update({
                    "status": "failed",
                    "error_message": "进程异常退出，任务自动恢复",
                    "finished_at": now.isoformat(),
                }).eq("task_id", task_id).eq("status", "running").execute()

                logger.warning(
                    f"ScheduledTaskScanner | recovered stale task | "
                    f"task_id={task_id} | new_status={new_status} | "
                    f"next_run={next_run}"
                )

        except Exception as e:
            logger.error(f"ScheduledTaskScanner recover error | {e}")

    async def _run_with_limit(self, task: dict) -> None:
        async with self._semaphore:
            try:
                await self._executor.execute(task)
            except Exception as e:
                logger.exception(
                    f"ScheduledTaskExecutor.execute crashed | "
                    f"task_id={task.get('id')} | error={e}"
                )
