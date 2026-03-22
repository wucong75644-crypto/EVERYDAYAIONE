"""
ERP 数据本地索引同步 Worker

独立 async task，与 BackgroundTaskWorker 并行运行。
通过 Redis 分布式锁保证多 Worker 部署下只有一个实例执行同步。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.0
"""

import asyncio
from datetime import datetime, timezone

from loguru import logger
from supabase import Client

from core.config import get_settings


class ErpSyncWorker:
    """ERP 同步调度器（独立 async task + Redis 分布式锁）"""

    # 高频同步类型（每轮逐个串行执行）
    # 串行避免 Supabase 连接过载（EOF SSL error），API 速率限制器保证 QPS 安全
    HIGH_FREQ_TYPES = [
        "product", "stock", "supplier",          # 已完成初始同步，增量快
        "purchase", "receipt", "shelf",           # 已完成初始同步，增量快
        "purchase_return",                         # 已完成初始同步，增量快
        "order", "aftersale",                      # 初始同步未完成，数据量大放最后
    ]
    # 低频同步类型
    LOW_FREQ_TYPES = ["platform_map"]

    # 日维护间隔（秒）：24小时
    DAILY_INTERVAL = 86400
    # 删除检测间隔（秒）：7天（商品删除低频，无需每天全量扫描）
    DELETION_INTERVAL = 604800

    def __init__(self, db: Client) -> None:
        self.db = db
        self.settings = get_settings()
        self.is_running = False
        self._lock_token: str | None = None
        self._last_platform_map_sync: datetime | None = None
        self._last_daily_maintenance: datetime | None = None
        self._last_deletion_detection: datetime | None = None

    async def start(self) -> None:
        """启动同步循环"""
        if not self.settings.erp_sync_enabled:
            logger.info("ErpSyncWorker disabled | erp_sync_enabled=False")
            return

        self.is_running = True
        logger.info(
            f"ErpSyncWorker started | interval={self.settings.erp_sync_interval}s | "
            f"platform_map_interval={self.settings.erp_platform_map_interval}s"
        )

        # 启动聚合消费者协程（串行从 Redis 队列取 key 做聚合）
        agg_task = asyncio.create_task(self._aggregation_consumer())

        while self.is_running:
            try:
                await self._run_sync_round()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ErpSyncWorker round error | error={e}", exc_info=True)

            await asyncio.sleep(self.settings.erp_sync_interval)

        agg_task.cancel()
        logger.info("ErpSyncWorker stopped")

    async def stop(self) -> None:
        """停止同步循环"""
        self.is_running = False
        await self._release_lock()

    async def _run_sync_round(self) -> None:
        """执行一轮同步（获取锁→逐个串行→释放锁）

        所有类型串行执行，避免 Supabase 连接过载（EOF SSL error）。
        API 调用由全局速率限制器限流（≤12 QPS）。
        已完成初始同步的类型增量很快（通常 <5s），大类型放最后。
        platform_map 依赖 product 数据，在全部完成后单独执行。
        """
        if not await self._acquire_lock():
            return  # 其他 Worker 在执行，跳过

        try:
            # 逐个串行执行（避免 DB 连接过载）
            for sync_type in self.HIGH_FREQ_TYPES:
                if not self.is_running:
                    break
                await self._extend_lock()
                await self._execute_sync(sync_type)

            # platform_map 依赖 product 表，必须在 product 同步完成后执行
            if self.is_running and self._should_run_low_freq():
                await self._extend_lock()
                await self._execute_sync("platform_map")
                self._last_platform_map_sync = datetime.now(timezone.utc)

            # 日维护任务（归档 + 聚合兜底 + 删除检测，每24小时）
            if self.is_running and self._should_run_daily():
                await self._extend_lock()
                await self._run_daily_maintenance()
                self._last_daily_maintenance = datetime.now(timezone.utc)
        finally:
            await self._release_lock()

    async def _execute_sync(self, sync_type: str) -> None:
        """执行单个类型的同步（委托给 ErpSyncService）"""
        try:
            await self._extend_lock()  # 并行任务启动时续期锁
            from services.kuaimai.erp_sync_service import ErpSyncService
            service = ErpSyncService(self.db, lock_extend_fn=self._extend_lock)
            await service.sync(sync_type)
        except Exception as e:
            logger.error(
                f"ERP sync failed | sync_type={sync_type} | error={e}",
                exc_info=True,
            )

    def _should_run_low_freq(self) -> bool:
        """判断低频任务是否到期"""
        if self._last_platform_map_sync is None:
            return True
        elapsed = (
            datetime.now(timezone.utc) - self._last_platform_map_sync
        ).total_seconds()
        return elapsed >= self.settings.erp_platform_map_interval

    def _should_run_daily(self) -> bool:
        """判断日维护任务是否到期"""
        if self._last_daily_maintenance is None:
            return True
        elapsed = (
            datetime.now(timezone.utc) - self._last_daily_maintenance
        ).total_seconds()
        return elapsed >= self.DAILY_INTERVAL

    def _should_run_deletion(self) -> bool:
        """判断删除检测是否到期（每周一次）"""
        if self._last_deletion_detection is None:
            return True
        elapsed = (
            datetime.now(timezone.utc) - self._last_deletion_detection
        ).total_seconds()
        return elapsed >= self.DELETION_INTERVAL

    # ── 日维护任务 ────────────────────────────────────────

    async def _run_daily_maintenance(self) -> None:
        """执行日维护：归档 → 聚合兜底 → 删除检测"""
        logger.info("ERP daily maintenance started")
        try:
            archived = await self._run_archive()
            reagg = await self._run_daily_reaggregation()
            deleted = 0
            if self._should_run_deletion():
                deleted = await self._run_deletion_detection()
                self._last_deletion_detection = datetime.now(timezone.utc)
            logger.info(
                f"ERP daily maintenance done | archived={archived} | "
                f"reaggregated={reagg} | deleted={deleted}"
            )
        except Exception as e:
            logger.error(f"ERP daily maintenance error | error={e}", exc_info=True)

    async def _run_archive(self) -> int:
        """
        热表→冷表归档（设计文档 §5.1）

        分批 SELECT→UPSERT(archive)→DELETE(hot)，upsert 幂等保证安全。
        """
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.settings.erp_archive_retention_days)
        ).isoformat()

        total_archived = 0
        batch_size = 1000

        while True:
            try:
                # 查询待归档行（按 id 分批）
                result = (
                    self.db.table("erp_document_items")
                    .select("*")
                    .lt("doc_modified_at", cutoff)
                    .limit(batch_size)
                    .execute()
                )
                rows = result.data or []
                if not rows:
                    break

                # UPSERT 到归档表（幂等）
                self.db.table("erp_document_items_archive").upsert(
                    rows,
                    on_conflict="doc_type,doc_id,item_index",
                ).execute()

                # DELETE 已归档行
                ids = [r["id"] for r in rows]
                self.db.table("erp_document_items").delete().in_(
                    "id", ids,
                ).execute()

                total_archived += len(rows)
            except Exception as e:
                logger.error(f"Archive batch failed | error={e}")
                break

        return total_archived

    async def _run_daily_reaggregation(self) -> int:
        """
        每日聚合兜底（设计文档 §5.1）

        对近7天的 (outer_id, stat_date) 重新聚合，修复遗漏。
        """
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).strftime("%Y-%m-%d")

        try:
            # 获取近7天所有受影响的 (outer_id, stat_date) 对
            result = self.db.rpc(
                "erp_aggregate_daily_stats_batch",
                {"p_since_date": cutoff},
            ).execute()
            count = result.data if isinstance(result.data, int) else 0
            return count
        except Exception as e:
            # RPC 不存在时降级为逐条聚合
            logger.warning(f"Batch reaggregation unavailable, fallback | error={e}")
            return await self._reaggregate_fallback(cutoff)

    async def _reaggregate_fallback(self, since_date: str) -> int:
        """逐条降级聚合：查询近7天的 distinct (outer_id, date) 并逐一重算"""
        try:
            from services.kuaimai.erp_sync_service import ErpSyncService
            svc = ErpSyncService(self.db)

            result = (
                self.db.table("erp_document_items")
                .select("outer_id,doc_created_at")
                .gte("doc_created_at", since_date)
                .not_.is_("outer_id", "null")
                .execute()
            )
            rows = result.data or []
            keys = svc.collect_affected_keys(rows)
            svc.run_aggregation(keys)
            return len(keys)
        except Exception as e:
            logger.error(f"Fallback reaggregation failed | error={e}")
            return 0

    async def _run_deletion_detection(self) -> int:
        """
        商品删除检测（设计文档任务3.5）

        全量拉取 API 商品列表 vs DB，标记已删除商品 active_status=-1。
        """
        try:
            from services.kuaimai.erp_sync_service import ErpSyncService
            svc = ErpSyncService(self.db)

            # 全量拉取 API 商品的 outer_id 集合（pageSize=500 减少调用次数）
            products = await svc.fetch_all_pages(
                "item.list.query",
                {"startModified": "2020-01-01 00:00:00", "endModified": "2099-12-31 23:59:59"},
                response_key="items",
                page_size=500,
            )
            api_ids = {p.get("outerId") for p in products if p.get("outerId")}

            # 获取 DB 中所有 active 商品 outer_id
            result = (
                self.db.table("erp_products")
                .select("outer_id")
                .neq("active_status", -1)
                .execute()
            )
            db_ids = {r["outer_id"] for r in (result.data or [])}

            # 标记不在 API 中的商品为已删除
            deleted_ids = db_ids - api_ids
            if not deleted_ids:
                return 0

            count = 0
            for outer_id in deleted_ids:
                try:
                    self.db.table("erp_products").update({
                        "active_status": -1,
                    }).eq("outer_id", outer_id).execute()
                    count += 1
                except Exception as e:
                    logger.warning(f"Mark deleted failed | outer_id={outer_id} | error={e}")

            if count:
                logger.info(f"Product deletion detected | count={count}")
            return count
        except Exception as e:
            logger.error(f"Deletion detection failed | error={e}")
            return 0

    # ── 分布式锁管理 ──────────────────────────────────────

    async def _acquire_lock(self) -> bool:
        """
        获取分布式锁（Redis 优先，DB 降级）

        Returns:
            True=获取成功可执行同步，False=其他Worker在执行应跳过
        """
        # 优先 Redis 锁
        try:
            from core.redis import RedisClient
            token = await RedisClient.acquire_lock(
                "erp_sync", timeout=self.settings.erp_sync_lock_ttl
            )
            if token:
                self._lock_token = token
                return True
            return False  # 其他 Worker 持锁
        except Exception as e:
            logger.warning(f"Redis lock unavailable, fallback to DB | error={e}")
            return await self._acquire_db_lock()

    async def _acquire_db_lock(self) -> bool:
        """
        DB 锁降级：原子 CAS UPDATE ... RETURNING

        避免 SELECT→UPDATE 的 TOCTOU 竞态（设计文档 §7.0 NEW-3）。
        """
        try:
            result = self.db.rpc(
                "erp_try_acquire_sync_lock",
                {"p_lock_ttl_seconds": self.settings.erp_sync_lock_ttl},
            ).execute()
            acquired = bool(result.data)
            if acquired:
                logger.debug("DB lock acquired for erp_sync")
            return acquired
        except Exception as e:
            # DB 也不可用，保守跳过本轮
            logger.error(f"DB lock failed | error={e}")
            return False

    async def _extend_lock(self) -> None:
        """续期分布式锁（防止长时间同步导致锁过期）"""
        if self._lock_token:
            try:
                from core.redis import RedisClient
                ok = await RedisClient.extend_lock(
                    "erp_sync", self._lock_token, self.settings.erp_sync_lock_ttl,
                )
                if not ok:
                    logger.warning("ERP sync lock extend failed (token mismatch)")
            except Exception:
                pass

    async def _release_lock(self) -> None:
        """释放 Redis 锁（DB 锁通过 TTL 自动释放）"""
        if self._lock_token:
            try:
                from core.redis import RedisClient
                await RedisClient.release_lock("erp_sync", self._lock_token)
            except Exception:
                pass  # Redis 不可用时锁会 TTL 过期
            self._lock_token = None

    # ── 聚合队列消费者 ────────────────────────────────────

    AGGREGATION_QUEUE_KEY = "erp:aggregation_queue"

    async def _aggregation_consumer(self) -> None:
        """串行消费 Redis 聚合队列，逐条调用 RPC，不阻塞数据拉取"""
        import json
        from core.redis import RedisClient

        logger.info("Aggregation consumer started")
        idle_interval = 10  # 队列空时轮询间隔（秒），避免打爆 Upstash
        while self.is_running:
            try:
                redis = await RedisClient.get_client()
                # 批量取最多 50 条，减少 Upstash 请求次数
                items = []
                for _ in range(50):
                    item = await redis.lpop(self.AGGREGATION_QUEUE_KEY)
                    if not item:
                        break
                    items.append(item)

                if not items:
                    await asyncio.sleep(idle_interval)
                    continue

                for item in items:
                    data = json.loads(item)
                    try:
                        self.db.rpc(
                            "erp_aggregate_daily_stats",
                            {"p_outer_id": data["outer_id"], "p_stat_date": data["stat_date"]},
                        ).execute()
                    except Exception as e:
                        logger.warning(
                            f"Aggregation consumer error | outer_id={data['outer_id']} | "
                            f"date={data['stat_date']} | error={e}"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Aggregation consumer loop error | error={e}")
                await asyncio.sleep(30)

        logger.info("Aggregation consumer stopped")
