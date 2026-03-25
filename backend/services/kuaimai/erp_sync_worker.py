"""
ERP 数据本地索引同步 Worker

独立 async task，与 BackgroundTaskWorker 并行运行。
通过 Redis 分布式锁保证多 Worker 部署下只有一个实例执行同步。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.0
"""

import asyncio
from datetime import datetime, timezone

from loguru import logger


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

    def __init__(self, db) -> None:
        self.db = db
        self.settings = get_settings()
        self.is_running = False
        self._lock_token: str | None = None
        self._last_platform_map_sync: datetime | None = None
        self._last_stock_full_refresh: datetime | None = None
        self._last_daily_maintenance: datetime | None = None
        self._last_deletion_detection: datetime | None = None
        # 内存聚合队列（替代 Redis 队列，消除网络超时问题）
        self.aggregation_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=10000)

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

        # 启动死信消费者协程（指数退避重试失败的 detail 调用）
        from services.kuaimai.erp_sync_dead_letter import consume_dead_letters
        dl_task = asyncio.create_task(
            consume_dead_letters(self.db, lambda: self.is_running)
        )

        while self.is_running:
            try:
                await self._run_sync_round()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ErpSyncWorker round error | error={e}", exc_info=True)

            await asyncio.sleep(self.settings.erp_sync_interval)

        agg_task.cancel()
        dl_task.cancel()
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

            # 库存全量刷新兜底（按配置间隔，默认每小时）
            if self.is_running and self._should_run_stock_full():
                await self._extend_lock()
                await self._execute_stock_full_refresh()
                self._last_stock_full_refresh = datetime.now()

            # platform_map 依赖 product 表，必须在 product 同步完成后执行
            if self.is_running and self._should_run_low_freq():
                await self._extend_lock()
                await self._execute_sync("platform_map")
                self._last_platform_map_sync = datetime.now()

            # 日维护任务（归档 + 聚合兜底 + 删除检测，每24小时）
            if self.is_running and self._should_run_daily():
                await self._extend_lock()
                await self._run_daily_maintenance()
                self._last_daily_maintenance = datetime.now()
        finally:
            await self._release_lock()

    async def _execute_sync(self, sync_type: str) -> None:
        """执行单个类型的同步（委托给 ErpSyncService）"""
        try:
            await self._extend_lock()  # 并行任务启动时续期锁
            from services.kuaimai.erp_sync_service import ErpSyncService
            service = ErpSyncService(self.db, lock_extend_fn=self._extend_lock, aggregation_queue=self.aggregation_queue)
            await service.sync(sync_type)
        except Exception as e:
            logger.error(
                f"ERP sync failed | sync_type={sync_type} | error={e}",
                exc_info=True,
            )

    def _should_run_stock_full(self) -> bool:
        """判断库存全量刷新是否到期"""
        if self._last_stock_full_refresh is None:
            return True
        elapsed = (
            datetime.now() - self._last_stock_full_refresh
        ).total_seconds()
        return elapsed >= self.settings.erp_stock_full_refresh_interval

    async def _execute_stock_full_refresh(self) -> None:
        """执行库存全量刷新（委托给 sync_stock_full）"""
        try:
            from services.kuaimai.erp_sync_master_handlers import sync_stock_full
            from services.kuaimai.erp_sync_service import ErpSyncService
            service = ErpSyncService(
                self.db, lock_extend_fn=self._extend_lock,
                aggregation_queue=self.aggregation_queue,
            )
            count = await sync_stock_full(service)
            if count > 0:
                logger.info(f"Stock full refresh done | synced={count}")
        except Exception as e:
            logger.error(f"Stock full refresh failed | error={e}", exc_info=True)

    def _should_run_low_freq(self) -> bool:
        """判断低频任务是否到期"""
        if self._last_platform_map_sync is None:
            return True
        elapsed = (
            datetime.now() - self._last_platform_map_sync
        ).total_seconds()
        return elapsed >= self.settings.erp_platform_map_interval

    def _should_run_daily(self) -> bool:
        """判断日维护任务是否到期"""
        if self._last_daily_maintenance is None:
            return True
        elapsed = (
            datetime.now() - self._last_daily_maintenance
        ).total_seconds()
        return elapsed >= self.DAILY_INTERVAL

    def _should_run_deletion(self) -> bool:
        """判断删除检测是否到期（每周一次）"""
        if self._last_deletion_detection is None:
            return True
        elapsed = (
            datetime.now() - self._last_deletion_detection
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
                self._last_deletion_detection = datetime.now()
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
            datetime.now() - timedelta(days=self.settings.erp_archive_retention_days)
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
            datetime.now() - timedelta(days=7)
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

    @staticmethod
    def _collect_api_product_ids(
        products: list[dict],
    ) -> tuple[set[str], set[str]]:
        """从 API 商品列表收集 SPU 和 SKU ID 集合"""
        spu_ids: set[str] = set()
        sku_ids: set[str] = set()
        for p in products:
            outer_id = p.get("outerId")
            if outer_id:
                spu_ids.add(outer_id)
            for sku in p.get("skus") or []:
                sku_id = sku.get("skuOuterId")
                if sku_id:
                    sku_ids.add(sku_id)
        return spu_ids, sku_ids

    def _mark_deleted_items(
        self, table: str, id_col: str, deleted_ids: set[str],
    ) -> int:
        """批量标记已删除的 SPU/SKU（active_status=-1）"""
        count = 0
        for item_id in deleted_ids:
            try:
                self.db.table(table).update({
                    "active_status": -1,
                }).eq(id_col, item_id).execute()
                count += 1
            except Exception as e:
                logger.warning(
                    f"Mark deleted failed | {id_col}={item_id} | error={e}"
                )
        return count

    async def _run_deletion_detection(self) -> int:
        """
        商品删除检测（SPU + SKU 级别）

        全量拉取 API 商品列表 vs DB，标记已删除的 SPU 和 SKU active_status=-1。
        """
        try:
            from services.kuaimai.erp_sync_service import ErpSyncService
            svc = ErpSyncService(self.db)

            products = await svc.fetch_all_pages(
                "item.list.query",
                {"startModified": "2020-01-01 00:00:00", "endModified": "2099-12-31 23:59:59"},
                response_key="items",
                page_size=100,
            )

            api_spu_ids, api_sku_ids = self._collect_api_product_ids(products)
            count = 0

            # SPU 删除检测
            result = (
                self.db.table("erp_products")
                .select("outer_id")
                .neq("active_status", -1)
                .execute()
            )
            db_spu_ids = {r["outer_id"] for r in (result.data or [])}
            deleted_spus = db_spu_ids - api_spu_ids
            count += self._mark_deleted_items("erp_products", "outer_id", deleted_spus)
            if deleted_spus:
                logger.info(f"SPU deletion detected | count={len(deleted_spus)}")

            # SKU 删除检测（分页加载）
            db_sku_ids: set[str] = set()
            offset = 0
            while True:
                r = (
                    self.db.table("erp_product_skus")
                    .select("sku_outer_id")
                    .neq("active_status", -1)
                    .range(offset, offset + 999)
                    .execute()
                )
                if not r.data:
                    break
                for row in r.data:
                    db_sku_ids.add(row["sku_outer_id"])
                offset += 1000
                if len(r.data) < 1000:
                    break

            deleted_skus = db_sku_ids - api_sku_ids
            count += self._mark_deleted_items("erp_product_skus", "sku_outer_id", deleted_skus)
            if deleted_skus:
                logger.info(f"SKU deletion detected | count={len(deleted_skus)}")

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

    async def _aggregation_consumer(self) -> None:
        """串行消费内存聚合队列，逐条调用 RPC，不阻塞数据拉取"""
        logger.info("Aggregation consumer started")
        while self.is_running:
            try:
                # 阻塞等待，1 秒超时（避免 shutdown 时卡住）
                try:
                    outer_id, stat_date = await asyncio.wait_for(
                        self.aggregation_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                try:
                    self.db.rpc(
                        "erp_aggregate_daily_stats",
                        {"p_outer_id": outer_id, "p_stat_date": stat_date},
                    ).execute()
                except Exception as e:
                    logger.warning(
                        f"Aggregation consumer error | outer_id={outer_id} | "
                        f"date={stat_date} | error={e}"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Aggregation consumer error | error={e}")
                await asyncio.sleep(5)

        logger.info("Aggregation consumer stopped")
