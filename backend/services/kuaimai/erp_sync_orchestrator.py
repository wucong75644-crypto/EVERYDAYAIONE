"""
ERP 同步编排器

统一管理 Scheduler + WorkerPool + AggregationConsumer + DeadLetterConsumer
的生命周期。main.py 只需创建并 start/stop 这个类。

Redis 不可用时自动降级为串行模式（复用旧 ErpSyncWorker 逻辑）。
Redis 恢复后自动热切换回队列模式（graceful stop → restart）。
"""

from __future__ import annotations

import asyncio

from loguru import logger

from core.config import get_settings

# Redis 恢复探测间隔（秒）
_REDIS_PROBE_INTERVAL = 300


class ErpSyncOrchestrator:
    """ERP 同步总编排器"""

    def __init__(self, db) -> None:
        self.db = db
        self.settings = get_settings()
        self.is_running = False
        self._mode: str = "idle"  # "queue" | "fallback" | "idle"
        self._tasks: list[asyncio.Task] = []
        # 内存聚合队列（所有 Worker 共享）
        self.aggregation_queue: asyncio.Queue[tuple[str, str, str | None]] = (
            asyncio.Queue(maxsize=10000)
        )
        self.aggregation_pending: set[tuple[str, str, str | None]] = set()

    async def start(self) -> None:
        """启动所有子系统"""
        if not self.settings.erp_sync_enabled:
            logger.info("ERP sync disabled | erp_sync_enabled=False")
            return

        self.is_running = True

        # 检测 Redis 可用性，决定运行模式
        if await self._is_redis_available():
            await self._start_queue_mode()
        else:
            await self._start_fallback_mode()

    async def stop(self) -> None:
        """优雅停止所有子系统"""
        self.is_running = False
        await self._stop_current_mode()
        logger.info("ErpSyncOrchestrator stopped")

    # ── 队列模式（正常路径）──────────────────────────

    async def _start_queue_mode(self) -> None:
        """Redis 可用：Scheduler + WorkerPool + 消费者"""
        from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
        from services.kuaimai.erp_sync_worker_pool import ErpSyncWorkerPool

        self._mode = "queue"
        logger.info(
            f"ErpSyncOrchestrator started (queue mode) | "
            f"workers={self.settings.erp_sync_worker_count} "
            f"max_org_concurrency={self.settings.erp_sync_max_org_concurrency}"
        )

        # 创建 Scheduler
        self._scheduler = ErpSyncScheduler(self.db)

        # 创建 WorkerPool
        self._worker_pool = ErpSyncWorkerPool(
            self.db,
            scheduler=self._scheduler,
            aggregation_queue=self.aggregation_queue,
            aggregation_pending=self.aggregation_pending,
        )

        # 启动各协程
        self._tasks.append(
            asyncio.create_task(self._scheduler.start())
        )
        self._tasks.append(
            asyncio.create_task(self._worker_pool.start())
        )
        self._tasks.append(
            asyncio.create_task(self._aggregation_consumer())
        )
        self._tasks.append(
            asyncio.create_task(self._dead_letter_consumer())
        )

    # ── 降级模式（Redis 不可用）────────────────────────

    async def _start_fallback_mode(self) -> None:
        """Redis 不可用：降级为串行模式 + 启动 Redis 恢复探测"""
        self._mode = "fallback"
        logger.warning(
            "ErpSyncOrchestrator started (FALLBACK serial mode) | "
            "Redis unavailable, using legacy ErpSyncWorker"
        )

        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        self._fallback_worker = ErpSyncWorker(self.db)
        self._tasks.append(
            asyncio.create_task(self._fallback_worker.start())
        )
        # 启动 Redis 恢复探测协程
        self._tasks.append(
            asyncio.create_task(self._redis_recovery_probe())
        )

    async def _redis_recovery_probe(self) -> None:
        """周期探测 Redis 恢复，自动热切换到队列模式。

        流程：
        1. 探测到 Redis 恢复
        2. 优雅停止 fallback Worker（等当前任务完成）
        3. 启动队列模式（Scheduler + WorkerPool）
        per-(org, sync_type) 锁兜底防止切换瞬间的任务重叠。
        """
        while self.is_running and self._mode == "fallback":
            await asyncio.sleep(_REDIS_PROBE_INTERVAL)

            if not self.is_running:
                break

            if not await self._is_redis_available():
                continue

            logger.info("Redis recovered, switching from fallback to queue mode")

            # 1. 优雅停止 fallback Worker
            try:
                await self._stop_current_mode()
            except Exception as e:
                logger.error(f"Failed to stop fallback worker | error={e}")
                continue

            # 2. 启动队列模式
            try:
                await self._start_queue_mode()
                logger.info("Successfully switched to queue mode")
                return  # 探测任务完成，退出
            except Exception as e:
                logger.error(f"Failed to start queue mode | error={e}")
                # 回退到 fallback
                await self._start_fallback_mode()
                return

    # ── 模式切换 ──────────────────────────────────────

    async def _stop_current_mode(self) -> None:
        """停止当前运行模式的所有组件"""
        # 停止子组件
        if hasattr(self, "_scheduler"):
            await self._scheduler.stop()
            del self._scheduler
        if hasattr(self, "_worker_pool"):
            await self._worker_pool.stop()
            del self._worker_pool
        if hasattr(self, "_fallback_worker"):
            await self._fallback_worker.stop()
            del self._fallback_worker

        # 取消所有协程任务
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()
        self._mode = "idle"

    # ── 聚合消费者 ────────────────────────────────────

    async def _aggregation_consumer(self) -> None:
        """串行消费内存聚合队列，逐条调用 RPC"""
        logger.info("Aggregation consumer started")
        while self.is_running:
            try:
                try:
                    outer_id, stat_date, agg_org_id = await asyncio.wait_for(
                        self.aggregation_queue.get(), timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                try:
                    await self.db.rpc(
                        "erp_aggregate_daily_stats",
                        {
                            "p_outer_id": outer_id,
                            "p_stat_date": stat_date,
                            "p_org_id": agg_org_id,
                        },
                    ).execute()
                except Exception as e:
                    logger.warning(
                        f"Aggregation consumer error | outer_id={outer_id} | "
                        f"date={stat_date} | org_id={agg_org_id} | error={e}"
                    )
                finally:
                    self.aggregation_pending.discard(
                        (outer_id, stat_date, agg_org_id)
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Aggregation consumer error | error={e}")
                await asyncio.sleep(5)

        logger.info("Aggregation consumer stopped")

    # ── 死信消费者 ────────────────────────────────────

    async def _dead_letter_consumer(self) -> None:
        """死信队列消费者（指数退避重试）"""
        from services.kuaimai.erp_sync_dead_letter import consume_dead_letters
        await consume_dead_letters(self.db, lambda: self.is_running)

    # ── 工具方法 ──────────────────────────────────────

    @staticmethod
    async def _is_redis_available() -> bool:
        """检测 Redis 是否可用"""
        try:
            from core.redis import RedisClient
            return await RedisClient.health_check()
        except Exception:
            return False
