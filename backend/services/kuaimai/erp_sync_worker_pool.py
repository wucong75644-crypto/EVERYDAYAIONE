"""
ERP 同步协程工作池

N 个 asyncio 协程并发从 Redis Sorted Set 取任务执行。
每个任务用 per-(org_id, sync_type) Redis 锁保证不重复。
单企业并发数受 max_org_concurrency 限制，防大企业霸占 Worker。

Redis 不可用时降级为串行模式（保留现有 ErpSyncWorker 逻辑）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from loguru import logger

from core.config import get_settings


class LockLostError(Exception):
    """任务锁已丢失（续期失败/被其他 Worker 抢占），应中止当前任务。"""
    pass


class ErpSyncWorkerPool:
    """协程工作池：并发消费 Redis 任务队列。"""

    def __init__(
        self,
        db,
        scheduler: "ErpSyncScheduler",
        aggregation_queue: asyncio.Queue,
        aggregation_pending: set,
    ) -> None:
        from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
        from services.kuaimai.erp_sync_executor import ErpSyncExecutor
        self.db = db
        self.settings = get_settings()
        self.scheduler: ErpSyncScheduler = scheduler
        self.aggregation_queue = aggregation_queue
        self.aggregation_pending = aggregation_pending
        self.is_running = False
        # 长生命周期 Executor（保持 _org_last_deletion 状态）
        self._executor = ErpSyncExecutor(
            db, aggregation_queue=aggregation_queue,
            aggregation_pending=aggregation_pending,
        )
        # per-org 并发计数 key 前缀
        self._concurrency_prefix = "erp_sync:concurrency"
        # 已持有的锁 {lock_key: token}，stop() 时统一释放
        self._held_locks: dict[str, str] = {}

    async def start(self) -> None:
        """启动 N 个 Worker 协程"""
        self.is_running = True
        n = self.settings.erp_sync_worker_count
        logger.info(f"ErpSyncWorkerPool started | workers={n}")

        workers = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(n)
        ]
        # 等待所有 worker（正常情况下不会结束，除非 is_running=False）
        await asyncio.gather(*workers, return_exceptions=True)
        logger.info("ErpSyncWorkerPool stopped")

    async def stop(self) -> None:
        """停止所有 Worker 并释放所有持有的锁"""
        self.is_running = False
        await self._release_all_locks()

    # ── Worker 主循环 ──────────────────────────────────

    async def _worker_loop(self, worker_id: int) -> None:
        """单个 Worker 协程的主循环"""
        while self.is_running:
            try:
                task = await self._dequeue()
                if task is None:
                    await asyncio.sleep(1)  # 队列空，短暂等待
                    continue

                task_id, _score = task
                await self._process_task(worker_id, task_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Worker {worker_id} error | error={e}", exc_info=True,
                )
                await asyncio.sleep(1)

    async def _process_task(self, worker_id: int, task_id: str) -> None:
        """处理单个任务：加锁 → 并发检查 → 执行 → 释放"""
        from services.kuaimai.erp_sync_scheduler import parse_task_id

        org_id, sync_type = parse_task_id(task_id)
        lock_key = f"erp_sync:{org_id or '__default__'}:{sync_type}"

        # 1. 检查企业并发上限
        if not await self._check_org_concurrency(org_id):
            logger.debug(
                f"Worker {worker_id} skip (org concurrency limit) | "
                f"org_id={org_id} sync_type={sync_type}"
            )
            await self._requeue_task(task_id)
            return

        # 2. 获取 per-(org, sync_type) 锁
        token = await self._acquire_task_lock(lock_key)
        if not token:
            await self._decr_org_concurrency(org_id)
            logger.debug(
                f"Worker {worker_id} skip (locked) | "
                f"org_id={org_id} sync_type={sync_type}"
            )
            await self._requeue_task(task_id)
            return

        start_ts = time.time()
        try:
            logger.info(
                f"Worker {worker_id} start | "
                f"org_id={org_id} sync_type={sync_type}"
            )
            await self._execute_task(org_id, sync_type, lock_key)
            duration = time.time() - start_ts
            logger.info(
                f"Worker {worker_id} done | "
                f"org_id={org_id} sync_type={sync_type} "
                f"duration={duration:.1f}s"
            )
            # 通知 Scheduler 更新时间戳（低频/特殊任务）
            self.scheduler.mark_completed(org_id, sync_type)
        except LockLostError as e:
            duration = time.time() - start_ts
            logger.warning(
                f"Worker {worker_id} aborted (lock lost) | "
                f"org_id={org_id} sync_type={sync_type} "
                f"duration={duration:.1f}s | {e}"
            )
            # 不调用 mark_completed → Scheduler 下轮重新入队
        except Exception as e:
            duration = time.time() - start_ts
            logger.error(
                f"Worker {worker_id} failed | "
                f"org_id={org_id} sync_type={sync_type} "
                f"duration={duration:.1f}s error={e}",
                exc_info=True,
            )
        finally:
            await self._release_task_lock(lock_key, token)
            await self._decr_org_concurrency(org_id)

    # ── 任务执行（委托给现有 Service 层）──────────────

    async def _execute_task(
        self, org_id: str | None, sync_type: str, lock_key: str,
    ) -> None:
        """执行单个同步任务，委托给 ErpSyncService。

        双重续期保障：
        1. 后台续期协程（TTL/2 间隔）：独立于 Service 调用节奏，
           解决「单个时间窗口执行超过 TTL」的问题。
           锁丢失时设置 lock_lost_event。
        2. Service 层 extend_fn：每个时间窗口完成后调用，
           检查 lock_lost_event，已丢失则 raise LockLostError 中止同步。
        """
        from services.kuaimai.client import KuaiMaiClient

        client = await self._create_client(org_id)
        if client is None:
            logger.warning(
                f"Skip task: client creation failed | org_id={org_id} sync_type={sync_type}"
            )
            return

        # 锁丢失信号（后台续期协程 set → extend_fn 检测 raise）
        lock_lost_event = asyncio.Event()

        # 构建锁续期闭包（绑定 lock_key + lock_lost_event）
        extend_fn = self._make_extend_fn(lock_key, lock_lost_event)

        # 启动后台续期协程
        renew_task = asyncio.create_task(
            self._lock_renew_loop(lock_key, lock_lost_event)
        )

        try:
            if sync_type == "daily_maintenance":
                await self._run_daily_maintenance(
                    org_id, client, extend_fn,
                )
            elif sync_type == "stock_full":
                await self._run_stock_full(org_id, client, extend_fn)
            elif sync_type == "order_reconcile":
                await self._run_order_reconcile(org_id, client, extend_fn)
            elif sync_type == "aftersale_reconcile":
                await self._run_aftersale_reconcile(org_id, client, extend_fn)
            else:
                await self._run_sync(sync_type, org_id, client, extend_fn)

            # stock 同步后触发套件库存视图刷新（throttle）
            if sync_type in ("stock", "stock_full"):
                await self._throttled_kit_refresh()
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
            if client:
                await client.close()

    async def _lock_renew_loop(
        self, lock_key: str, lock_lost_event: asyncio.Event,
    ) -> None:
        """后台锁续期协程：每 TTL/2 秒续期一次。

        锁丢失时 set lock_lost_event（通知 extend_fn 在下次调用时 raise）。
        不直接 cancel 主任务，避免中断 DB 事务。
        """
        interval = self.settings.erp_sync_task_lock_ttl / 2
        while True:
            await asyncio.sleep(interval)
            token = self._held_locks.get(lock_key)
            if not token or token == "__db_lock__":
                return  # DB 锁无续期机制

            try:
                from core.redis import RedisClient
                ok = await RedisClient.extend_lock(
                    lock_key, token,
                    self.settings.erp_sync_task_lock_ttl,
                )
                if not ok:
                    logger.warning(f"Lock lost in renew loop | key={lock_key}")
                    self._held_locks.pop(lock_key, None)
                    lock_lost_event.set()
                    return  # 停止续期
            except Exception as e:
                logger.warning(
                    f"Lock renew Redis error | key={lock_key} error={e}"
                )

    async def _run_sync(
        self, sync_type: str, org_id: str | None,
        client, extend_fn,
    ) -> None:
        """执行常规增量同步"""
        from core.org_scoped_db import OrgScopedDB
        from services.kuaimai.erp_sync_service import ErpSyncService
        scoped_db = OrgScopedDB(self.db, org_id)
        service = ErpSyncService(
            scoped_db,
            lock_extend_fn=extend_fn,
            aggregation_queue=self.aggregation_queue,
            aggregation_pending=self.aggregation_pending,
            org_id=org_id,
            client=client,
        )
        await service.sync(sync_type)

    async def _run_stock_full(
        self, org_id: str | None, client, extend_fn,
    ) -> None:
        """执行库存全量刷新"""
        from core.org_scoped_db import OrgScopedDB
        from services.kuaimai.erp_sync_master_handlers import sync_stock_full
        from services.kuaimai.erp_sync_service import ErpSyncService
        scoped_db = OrgScopedDB(self.db, org_id)
        service = ErpSyncService(
            scoped_db,
            lock_extend_fn=extend_fn,
            aggregation_queue=self.aggregation_queue,
            aggregation_pending=self.aggregation_pending,
            org_id=org_id,
            client=client,
        )
        count = await sync_stock_full(service)
        if count > 0:
            logger.info(f"Stock full refresh done | org_id={org_id} synced={count}")

    async def _run_daily_maintenance(
        self, org_id: str | None, client, extend_fn,
    ) -> None:
        """执行日维护：归档 + 聚合兜底 + 删除检测。

        使用长生命周期的 _executor（保持删除检测时间戳状态）。
        """
        await self._executor.run_daily_maintenance(org_id=org_id, client=client)

    async def _run_order_reconcile(
        self, org_id: str | None, client, extend_fn,
    ) -> None:
        """执行订单分层对账（委托给 executor）"""
        count = await self._executor.run_order_reconcile(
            org_id=org_id, client=client, lock_extend_fn=extend_fn,
        )
        if count > 0:
            logger.info(f"Order reconcile done | org_id={org_id} backfilled={count}")

    async def _run_aftersale_reconcile(
        self, org_id: str | None, client, extend_fn,
    ) -> None:
        """执行售后分层对账（委托给 executor）"""
        count = await self._executor.run_aftersale_reconcile(
            org_id=org_id, client=client, lock_extend_fn=extend_fn,
        )
        if count > 0:
            logger.info(f"Aftersale reconcile done | org_id={org_id} backfilled={count}")

    # ── 套件库存视图刷新（throttle）────────────────────

    async def _throttled_kit_refresh(self) -> None:
        """刷新套件库存物化视图（节流+并发锁双保险）

        1. Redis 节流：N秒内只允许一次（控制频率）
        2. pg_advisory_lock：同一时刻只有一个进程刷新（控制并发）
        """
        try:
            from core.redis import RedisClient
            if not await RedisClient.try_throttle(
                "erp_sync:kit_refresh",
                self.settings.erp_sync_kit_refresh_throttle,
            ):
                return  # 节流中，跳过

            async with self.db.pool.connection() as conn:
                await conn.set_autocommit(True)
                async with conn.cursor() as cur:
                    # advisory lock 防止多 worker 并发刷新
                    await cur.execute(
                        "SELECT pg_try_advisory_lock(hashtext('mv_kit_stock')) AS locked"
                    )
                    row = await cur.fetchone()
                    locked = row["locked"] if isinstance(row, dict) else row[0]
                    if not locked:
                        return  # 另一个 worker 正在刷新，跳过
                    try:
                        await cur.execute(
                            "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_kit_stock"
                        )
                    finally:
                        await cur.execute(
                            "SELECT pg_advisory_unlock(hashtext('mv_kit_stock'))"
                        )
            logger.debug("Kit stock materialized view refreshed")
        except Exception as e:
            logger.warning(f"Kit stock refresh failed | error={e}")

    # ── Client 创建 ───────────────────────────────────

    async def _create_client(self, org_id: str | None):
        """为企业创建 KuaiMaiClient（带 token 双写闭环）。

        三层 token 来源优先级：
        1. Redis 热缓存（load_cached_token）— 最新值，跨进程共享
        2. DB org_configs（resolver.get_erp_credentials）— 持久化兜底
        3. 内存（client 实例字段）— 当前任务期间使用

        refresh 触发时通过 token_persister 回调写回 DB，
        防止 Redis 失效后回退到初始死态 token（这是上次雪崩的根因）。
        """
        from services.kuaimai.client import KuaiMaiClient

        if org_id is None:
            # 散客无 ERP，不降级到系统默认凭证
            return None

        try:
            from services.org.config_resolver import AsyncOrgConfigResolver
            resolver = AsyncOrgConfigResolver(self.db)
            creds = await resolver.get_erp_credentials(org_id)

            # token 持久化回调闭包：refresh 成功后通过这个回调写回 org_configs
            async def _persist(oid: str, access: str, refresh: str) -> None:
                await resolver.update_erp_token(oid, access, refresh)

            client = KuaiMaiClient(
                app_key=creds["kuaimai_app_key"],
                app_secret=creds["kuaimai_app_secret"],
                access_token=creds["kuaimai_access_token"],
                refresh_token=creds["kuaimai_refresh_token"],
                org_id=org_id,
                token_persister=_persist,
            )
            # 从 Redis 加载最新热缓存（可能比 DB 更新，因为其他 worker 刚 refresh 过）
            await client.load_cached_token()
            return client
        except (ValueError, KeyError) as e:
            logger.warning(f"Cannot create client for org | org_id={org_id} error={e}")
            return None

    # ── Redis 队列操作 ────────────────────────────────

    async def _dequeue(self) -> tuple[str, float] | None:
        """从队列取出优先级最高的任务"""
        try:
            from core.redis import RedisClient
            return await RedisClient.dequeue_task(
                self.settings.erp_sync_queue_key,
            )
        except Exception:
            return None

    async def _requeue_task(self, task_id: str) -> None:
        """将无法执行的任务放回队列（延迟 5 秒再被取出）"""
        try:
            from core.redis import RedisClient
            import time
            delay_score = time.time() + 5  # 5 秒后再被取出
            await RedisClient.enqueue_task(
                self.settings.erp_sync_queue_key, task_id, delay_score,
            )
        except Exception:
            pass  # 放回失败不影响主流程，Scheduler 下轮会重新入队

    # ── 分布式锁管理 ──────────────────────────────────

    async def _acquire_task_lock(self, lock_key: str) -> str | None:
        """获取 per-(org, sync_type) 任务锁"""
        try:
            from core.redis import RedisClient
            token = await RedisClient.acquire_lock(
                lock_key, timeout=self.settings.erp_sync_task_lock_ttl,
            )
            if token:
                self._held_locks[lock_key] = token
            return token
        except Exception as e:
            logger.warning(f"Task lock acquire failed | key={lock_key} error={e}")
            # DB 降级锁
            return await self._acquire_task_lock_db(lock_key)

    async def _acquire_task_lock_db(self, lock_key: str) -> str | None:
        """DB 锁降级：利用现有 RPC"""
        try:
            # 从 lock_key 解析 org_id
            parts = lock_key.split(":")
            org_id_str = parts[1] if len(parts) >= 3 else None
            org_id = None if org_id_str == "__default__" else org_id_str

            result = await self.db.rpc(
                "erp_try_acquire_sync_lock",
                {
                    "p_lock_ttl_seconds": self.settings.erp_sync_task_lock_ttl,
                    "p_org_id": org_id,
                },
            ).execute()
            if bool(result.data):
                # DB 锁用特殊 token 标记
                self._held_locks[lock_key] = "__db_lock__"
                return "__db_lock__"
            return None
        except Exception as e:
            logger.error(f"DB task lock failed | key={lock_key} error={e}")
            return None

    def _make_extend_fn(
        self, lock_key: str, lock_lost_event: asyncio.Event,
    ):
        """创建绑定当前任务 lock_key 的续期闭包。

        ErpSyncService 在每个时间窗口后调用 extend_fn。
        两个检测时机：
        1. lock_lost_event 已被后台续期协程 set → 立即 raise
        2. 自己调 extend_lock 失败 → set event + raise
        """
        async def _extend() -> None:
            # 后台续期协程已发现锁丢失
            if lock_lost_event.is_set():
                raise LockLostError(
                    f"Lock lost (detected by renew loop) | key={lock_key}"
                )

            token = self._held_locks.get(lock_key)
            if not token or token == "__db_lock__":
                return  # DB 锁无续期机制，靠 TTL

            try:
                from core.redis import RedisClient
                ok = await RedisClient.extend_lock(
                    lock_key, token,
                    self.settings.erp_sync_task_lock_ttl,
                )
                if not ok:
                    self._held_locks.pop(lock_key, None)
                    lock_lost_event.set()
                    raise LockLostError(
                        f"Lock lost (token mismatch) | key={lock_key}"
                    )
            except LockLostError:
                raise
            except Exception as e:
                # Redis 通信异常：保守处理，不中断任务
                logger.warning(f"Lock extend Redis error | key={lock_key} error={e}")
        return _extend

    async def _release_task_lock(self, lock_key: str, token: str) -> None:
        """释放任务锁"""
        self._held_locks.pop(lock_key, None)
        if token == "__db_lock__":
            return  # DB 锁通过 TTL 自动释放

        try:
            from core.redis import RedisClient
            await RedisClient.release_lock(lock_key, token)
        except Exception:
            pass

    async def _release_all_locks(self) -> None:
        """stop() 时释放所有持有的锁"""
        for lock_key, token in list(self._held_locks.items()):
            await self._release_task_lock(lock_key, token)
        self._held_locks.clear()

    # ── 企业并发限制 ──────────────────────────────────

    async def _check_org_concurrency(self, org_id: str | None) -> bool:
        """检查并递增企业并发计数，超限返回 False"""
        try:
            from core.redis import RedisClient
            key = f"{self._concurrency_prefix}:{org_id or '__default__'}"
            count = await RedisClient.incr_with_ttl(key, ttl=600)
            if count > self.settings.erp_sync_max_org_concurrency:
                # 超限，回退计数
                await RedisClient.decr_floor(key)
                return False
            return True
        except Exception:
            return True  # Redis 不可用时不限制

    async def _decr_org_concurrency(self, org_id: str | None) -> None:
        """递减企业并发计数"""
        try:
            from core.redis import RedisClient
            key = f"{self._concurrency_prefix}:{org_id or '__default__'}"
            await RedisClient.decr_floor(key)
        except Exception:
            pass
