"""
ERP 数据本地索引同步 Worker

独立 async task，与 BackgroundTaskWorker 并行运行。
通过 Redis 分布式锁保证多 Worker 部署下只有一个实例执行同步。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.0
"""

import asyncio
from datetime import datetime

from loguru import logger


from core.config import get_settings
from utils.time_context import now_cn


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
    # 配置数据同步类型（每天 2 次，全量刷新）
    CONFIG_TYPES = ["shop", "warehouse", "tag", "category", "logistics_company"]
    # 仓库单据同步类型（每 5 分钟，增量同步）
    WAREHOUSE_TYPES = [
        "allocate", "allocate_in", "allocate_out",
        "other_in", "other_out",
        "inventory_sheet", "unshelve", "process_order",
        "section_record", "goods_section",
    ]

    # 日维护间隔（秒）：24小时
    DAILY_INTERVAL = 86400
    # 删除检测间隔（秒）：7天（商品删除低频，无需每天全量扫描）
    DELETION_INTERVAL = 604800
    # 配置数据同步间隔（秒）：12小时（每天 2 次）
    CONFIG_INTERVAL = 43200
    # 仓库单据同步间隔（秒）：5分钟
    WAREHOUSE_INTERVAL = 300

    def __init__(self, db) -> None:
        self.db = db
        self.settings = get_settings()
        self.is_running = False
        self._lock_token: str | None = None
        # 按企业隔离的时间戳（key=org_id or None）
        self._org_last_platform_map: dict[str | None, datetime] = {}
        self._org_last_stock_full: dict[str | None, datetime] = {}
        self._org_last_daily: dict[str | None, datetime] = {}
        self._org_last_deletion: dict[str | None, datetime] = {}
        self._org_last_config: dict[str | None, datetime] = {}
        self._org_last_warehouse: dict[str | None, datetime] = {}
        # 内存聚合队列：三元组 (outer_id, stat_date, org_id)
        self.aggregation_queue: asyncio.Queue[tuple[str, str, str | None]] = asyncio.Queue(maxsize=10000)
        # 去重集合
        self.aggregation_pending: set[tuple[str, str, str | None]] = set()

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
        """执行一轮同步（获取锁→遍历企业→每个企业独立完整执行→释放锁）

        每个企业独立执行：高频同步 → 库存全量 → 低频同步 → 日维护。
        企业之间串行（避免 DB 过载），但状态互相隔离。
        """
        if not await self._acquire_lock():
            return

        try:
            orgs = await self._load_erp_orgs()

            for org_id, client in orgs:
                if not self.is_running:
                    break
                try:
                    await self._run_org_sync(org_id, client)
                except Exception as e:
                    logger.error(f"Org sync round failed | org_id={org_id} | error={e}", exc_info=True)
                finally:
                    if client:
                        await client.close()
        finally:
            await self._release_lock()

    async def _run_org_sync(self, org_id: str | None, client) -> None:
        """单个企业的完整同步周期"""
        logger.info(f"ERP sync org start | org_id={org_id}")

        # 高频同步
        for sync_type in self.HIGH_FREQ_TYPES:
            if not self.is_running:
                return
            await self._extend_lock()
            await self._execute_sync(sync_type, org_id=org_id, client=client)

        # 仓库单据同步（每 5 分钟）
        if self.is_running and self._should_run_warehouse(org_id):
            for wh_type in self.WAREHOUSE_TYPES:
                if not self.is_running:
                    break
                await self._extend_lock()
                await self._execute_sync(wh_type, org_id=org_id, client=client)
            self._org_last_warehouse[org_id] = now_cn()

        # 库存全量刷新 — 已禁用（套件商品无独立库存，由 mv_kit_stock 计算）
        # if self.is_running and self._should_run_stock_full(org_id):
        #     await self._extend_lock()
        #     await self._execute_stock_full_refresh(org_id=org_id, client=client)
        #     self._org_last_stock_full[org_id] = now_cn()

        # 低频同步（platform_map）
        if self.is_running and self._should_run_low_freq(org_id):
            await self._extend_lock()
            await self._execute_sync("platform_map", org_id=org_id, client=client)
            self._org_last_platform_map[org_id] = now_cn()

        # 配置数据同步（shop/warehouse/tag/category/logistics_company）
        if self.is_running and self._should_run_config(org_id):
            for config_type in self.CONFIG_TYPES:
                if not self.is_running:
                    break
                await self._extend_lock()
                await self._execute_sync(config_type, org_id=org_id, client=client)
            self._org_last_config[org_id] = now_cn()

        # 日维护（归档+聚合兜底+删除检测）
        if self.is_running and self._should_run_daily(org_id):
            await self._extend_lock()
            await self._run_daily_maintenance(org_id=org_id, client=client)
            self._org_last_daily[org_id] = now_cn()

        logger.info(f"ERP sync org done | org_id={org_id}")

    async def _load_erp_orgs(self) -> list[tuple[str | None, "KuaiMaiClient | None"]]:
        """加载所有 ERP 功能已开启的企业，返回 (org_id, client) 列表。

        无企业时降级为散客模式（全局凭证）。
        """
        from services.kuaimai.client import KuaiMaiClient

        orgs: list[tuple[str | None, KuaiMaiClient | None]] = []

        try:
            result = await (
                self.db.table("organizations")
                .select("id, features")
                .eq("status", "active")
                .execute()
            )
            for org in (result.data or []):
                features = org.get("features") or {}
                if not features.get("erp"):
                    continue
                org_id = str(org["id"])
                try:
                    from services.org.config_resolver import AsyncOrgConfigResolver
                    resolver = AsyncOrgConfigResolver(self.db)
                    creds = await resolver.get_erp_credentials(org_id)

                    # token 双写闭环：refresh 后回写 DB
                    # 闭包默认参数捕获当前 resolver 实例（防止循环引用问题）
                    async def _persist(
                        oid: str, access: str, refresh: str,
                        _r=resolver,
                    ) -> None:
                        await _r.update_erp_token(oid, access, refresh)

                    client = KuaiMaiClient(
                        app_key=creds["kuaimai_app_key"],
                        app_secret=creds["kuaimai_app_secret"],
                        access_token=creds["kuaimai_access_token"],
                        refresh_token=creds["kuaimai_refresh_token"],
                        org_id=org_id,
                        token_persister=_persist,
                    )
                    await client.load_cached_token()  # 从 Redis 拿最新热缓存
                    orgs.append((org_id, client))
                except ValueError as e:
                    logger.warning(f"Skip org {org_id} ERP sync: {e}")
        except Exception as e:
            logger.error(f"Failed to load ERP orgs | error={e}")

        # 无企业时降级：使用全局凭证（散客兼容）
        if not orgs:
            client = KuaiMaiClient()
            if client.is_configured:
                orgs.append((None, client))
            else:
                await client.close()

        return orgs

    async def _execute_sync(
        self, sync_type: str,
        org_id: str | None = None,
        client: "KuaiMaiClient | None" = None,
    ) -> None:
        """执行单个类型的同步（委托给 ErpSyncService）"""
        try:
            await self._extend_lock()
            from core.org_scoped_db import OrgScopedDB
            from services.kuaimai.erp_sync_service import ErpSyncService
            scoped_db = OrgScopedDB(self.db, org_id)
            service = ErpSyncService(
                scoped_db, lock_extend_fn=self._extend_lock,
                aggregation_queue=self.aggregation_queue,
                aggregation_pending=self.aggregation_pending,
                org_id=org_id,
                client=client,
            )
            await service.sync(sync_type)
            if sync_type == "stock":
                await self._refresh_kit_stock()
        except Exception as e:
            logger.error(
                f"ERP sync failed | sync_type={sync_type} | org_id={org_id} | error={e}",
                exc_info=True,
            )

    def _should_run_stock_full(self, org_id: str | None = None) -> bool:
        """判断库存全量刷新是否到期（按企业隔离计时）"""
        last = self._org_last_stock_full.get(org_id)
        if last is None:
            return True
        elapsed = (now_cn() - last).total_seconds()
        return elapsed >= self.settings.erp_stock_full_refresh_interval

    async def _execute_stock_full_refresh(
        self, org_id: str | None = None, client: "KuaiMaiClient | None" = None,
    ) -> None:
        """执行库存全量刷新（委托给 sync_stock_full）"""
        try:
            from core.org_scoped_db import OrgScopedDB
            from services.kuaimai.erp_sync_master_handlers import sync_stock_full
            from services.kuaimai.erp_sync_service import ErpSyncService
            scoped_db = OrgScopedDB(self.db, org_id)
            service = ErpSyncService(
                scoped_db, lock_extend_fn=self._extend_lock,
                aggregation_queue=self.aggregation_queue,
                aggregation_pending=self.aggregation_pending,
                org_id=org_id,
                client=client,
            )
            count = await sync_stock_full(service)
            if count > 0:
                logger.info(f"Stock full refresh done | synced={count}")
            await self._refresh_kit_stock()
        except Exception as e:
            logger.error(f"Stock full refresh failed | error={e}", exc_info=True)

    async def _refresh_kit_stock(self) -> None:
        """刷新套件库存物化视图（stock 同步后调用，~1s）"""
        try:
            async with self.db.pool.connection() as conn:
                await conn.set_autocommit(True)
                async with conn.cursor() as cur:
                    # advisory lock 防止多进程并发刷新
                    await cur.execute(
                        "SELECT pg_try_advisory_lock(hashtext('mv_kit_stock')) AS locked"
                    )
                    row = await cur.fetchone()
                    locked = row["locked"] if isinstance(row, dict) else row[0]
                    if not locked:
                        return  # 另一个进程正在刷新，跳过
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

    def _should_run_low_freq(self, org_id: str | None = None) -> bool:
        """判断低频任务是否到期（按企业隔离计时）"""
        last = self._org_last_platform_map.get(org_id)
        if last is None:
            return True
        elapsed = (now_cn() - last).total_seconds()
        return elapsed >= self.settings.erp_platform_map_interval

    def _should_run_warehouse(self, org_id: str | None = None) -> bool:
        """判断仓库单据同步是否到期（按企业隔离计时，每 5 分钟）"""
        last = self._org_last_warehouse.get(org_id)
        if last is None:
            return True
        elapsed = (now_cn() - last).total_seconds()
        return elapsed >= self.WAREHOUSE_INTERVAL

    def _should_run_config(self, org_id: str | None = None) -> bool:
        """判断配置数据同步是否到期（按企业隔离计时，每 12 小时）"""
        last = self._org_last_config.get(org_id)
        if last is None:
            return True
        elapsed = (now_cn() - last).total_seconds()
        return elapsed >= self.CONFIG_INTERVAL

    def _should_run_daily(self, org_id: str | None = None) -> bool:
        """判断日维护任务是否到期（按企业隔离计时）"""
        last = self._org_last_daily.get(org_id)
        if last is None:
            return True
        elapsed = (now_cn() - last).total_seconds()
        return elapsed >= self.DAILY_INTERVAL

    def _should_run_deletion(self, org_id: str | None = None) -> bool:
        """判断删除检测是否到期（每周一次，按企业隔离计时）"""
        last = self._org_last_deletion.get(org_id)
        if last is None:
            return True
        elapsed = (now_cn() - last).total_seconds()
        return elapsed >= self.DELETION_INTERVAL

    # ── 日维护任务 ────────────────────────────────────────

    async def _run_daily_maintenance(
        self, org_id: str | None = None, client=None,
    ) -> None:
        """执行日维护：归档 → 聚合兜底 → 删除检测（按企业隔离）"""
        logger.info(f"ERP daily maintenance started | org_id={org_id}")
        archived, reagg, deleted = 0, 0, 0

        try:
            archived = await self._run_archive(org_id=org_id)
        except Exception as e:
            logger.error(f"Daily maintenance: archive failed | org_id={org_id} | error={e}", exc_info=True)

        try:
            reagg = await self._run_daily_reaggregation(org_id=org_id)
        except Exception as e:
            logger.error(f"Daily maintenance: reaggregation failed | org_id={org_id} | error={e}", exc_info=True)

        try:
            if self._should_run_deletion(org_id):
                deleted = await self._run_deletion_detection(org_id=org_id, client=client)
                self._org_last_deletion[org_id] = now_cn()
        except Exception as e:
            logger.error(f"Daily maintenance: deletion detection failed | org_id={org_id} | error={e}", exc_info=True)

        logger.info(
            f"ERP daily maintenance done | org_id={org_id} | archived={archived} | "
            f"reaggregated={reagg} | deleted={deleted}"
        )

    async def _run_archive(self, org_id: str | None = None) -> int:
        """
        热表→冷表归档（设计文档 §5.1）

        分批 SELECT→UPSERT(archive)→DELETE(hot)，upsert 幂等保证安全。
        归档条件：doc_created_at 和 doc_modified_at 都超过保留期才归档。
        doc_created_at 保底：防止 modified 为 ERP 零值（如 2000-01-01）的
        补发单/手工单被误归档。

        注意：不使用 synced_at 作为归档条件——delete+insert 同步模式下
        synced_at 每轮都被刷新，导致归档条件永远不满足。
        """
        from datetime import timedelta
        cutoff = (
            now_cn() - timedelta(days=self.settings.erp_archive_retention_days)
        ).isoformat()

        total_archived = 0
        batch_size = 1000
        max_per_run = 100_000  # 每次日维护最多归档 10 万行，避免阻塞同步

        while total_archived < max_per_run:
            try:
                q = (
                    self.db.table("erp_document_items")
                    .select("*")
                    .lt("doc_modified_at", cutoff)
                    .lt("doc_created_at", cutoff)
                )
                result = await q.limit(batch_size).execute()
                rows = result.data or []
                if not rows:
                    break

                # UPSERT 到归档表（幂等）
                await self.db.table("erp_document_items_archive").upsert(
                    rows,
                    on_conflict="doc_type,doc_id,item_index,org_id",
                ).execute()

                # DELETE 已归档行
                ids = [r["id"] for r in rows]
                await self.db.table("erp_document_items").delete().in_(
                    "id", ids,
                ).execute()

                total_archived += len(rows)
            except Exception as e:
                logger.error(f"Archive batch failed | error={e}")
                break

        if total_archived >= max_per_run:
            logger.info(f"Archive hit per-run limit | archived={total_archived} max={max_per_run}")

        return total_archived

    async def _run_daily_reaggregation(self, org_id: str | None = None) -> int:
        """
        每日聚合兜底（设计文档 §5.1）

        对近7天的 (outer_id, stat_date) 重新聚合，修复遗漏。
        """
        from datetime import timedelta
        cutoff = (
            now_cn() - timedelta(days=7)
        ).strftime("%Y-%m-%d")

        try:
            # 获取近7天所有受影响的 (outer_id, stat_date) 对
            result = await self.db.rpc(
                "erp_aggregate_daily_stats_batch",
                {"p_since_date": cutoff, "p_org_id": org_id},
            ).execute()
            count = result.data if isinstance(result.data, int) else 0
            return count
        except Exception as e:
            # RPC 不存在时降级为逐条聚合
            logger.warning(f"Batch reaggregation unavailable, fallback | error={e}")
            return await self._reaggregate_fallback(cutoff, org_id=org_id)

    async def _reaggregate_fallback(self, since_date: str, org_id: str | None = None) -> int:
        """逐条降级聚合：查询近7天的 distinct (outer_id, date) 并逐一重算"""
        try:
            from core.org_scoped_db import OrgScopedDB
            from services.kuaimai.erp_sync_service import ErpSyncService
            scoped_db = OrgScopedDB(self.db, org_id)
            svc = ErpSyncService(
                scoped_db,
                aggregation_queue=self.aggregation_queue,
                aggregation_pending=self.aggregation_pending,
                org_id=org_id,
            )

            q = (
                self.db.table("erp_document_items")
                .select("outer_id,doc_created_at")
                .gte("doc_created_at", since_date)
                .not_.is_("outer_id", "null")
            )
            result = await q.execute()
            rows = result.data or []
            keys = svc.collect_affected_keys(rows)
            await svc.run_aggregation(keys)
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

    async def _paginated_select_ids(
        self, table: str, id_column: str, *, batch_size: int = 1000,
        org_id: str | None = None,
    ) -> set[str]:
        """分页加载表中所有活跃记录的指定列，返回 ID 集合（按企业过滤）"""
        ids: set[str] = set()
        offset = 0
        while True:
            q = self.db.table(table).select(id_column).neq("active_status", -1)
            r = await q.range(offset, offset + batch_size - 1).execute()
            if not r.data:
                break
            for row in r.data:
                ids.add(row[id_column])
            offset += batch_size
            if len(r.data) < batch_size:
                break
        return ids

    async def _mark_deleted_items(
        self, table: str, id_col: str, deleted_ids: set[str],
        org_id: str | None = None,
    ) -> int:
        """批量标记已删除的 SPU/SKU（active_status=-1，按企业隔离）"""
        count = 0
        for item_id in deleted_ids:
            try:
                q = self.db.table(table).update({
                    "active_status": -1,
                }).eq(id_col, item_id)
                await q.execute()
                count += 1
            except Exception as e:
                logger.warning(
                    f"Mark deleted failed | {id_col}={item_id} | error={e}"
                )
        return count

    async def _run_deletion_detection(
        self, org_id: str | None = None, client=None,
    ) -> int:
        """
        商品删除检测（SPU + SKU 级别，按企业隔离）

        全量拉取 API 商品列表 vs DB，标记已删除的 SPU 和 SKU active_status=-1。
        """
        try:
            from services.kuaimai.erp_sync_service import ErpSyncService
            from core.org_scoped_db import OrgScopedDB
            scoped_db = OrgScopedDB(self.db, org_id)
            svc = ErpSyncService(scoped_db, org_id=org_id, client=client)

            products = await svc.fetch_all_pages(
                "item.list.query",
                {"startModified": "2020-01-01 00:00:00", "endModified": "2099-12-31 23:59:59"},
                response_key="items",
                page_size=100,
            )

            api_spu_ids, api_sku_ids = self._collect_api_product_ids(products)
            count = 0

            # SPU 删除检测
            db_spu_ids = await self._paginated_select_ids("erp_products", "outer_id", org_id=org_id)
            deleted_spus = db_spu_ids - api_spu_ids
            count += await self._mark_deleted_items("erp_products", "outer_id", deleted_spus, org_id=org_id)
            if deleted_spus:
                logger.info(f"SPU deletion detected | org_id={org_id} | count={len(deleted_spus)}")

            # SKU 删除检测
            db_sku_ids = await self._paginated_select_ids("erp_product_skus", "sku_outer_id", org_id=org_id)
            deleted_skus = db_sku_ids - api_sku_ids
            count += await self._mark_deleted_items("erp_product_skus", "sku_outer_id", deleted_skus, org_id=org_id)
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
            result = await self.db.rpc(
                "erp_try_acquire_sync_lock",
                {"p_lock_ttl_seconds": self.settings.erp_sync_lock_ttl, "p_org_id": None},
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
                    outer_id, stat_date, agg_org_id = await asyncio.wait_for(
                        self.aggregation_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                try:
                    await self.db.rpc(
                        "erp_aggregate_daily_stats",
                        {"p_outer_id": outer_id, "p_stat_date": stat_date, "p_org_id": agg_org_id},
                    ).execute()
                except Exception as e:
                    logger.warning(
                        f"Aggregation consumer error | outer_id={outer_id} | "
                        f"date={stat_date} | org_id={agg_org_id} | error={e}"
                    )
                finally:
                    self.aggregation_pending.discard((outer_id, stat_date, agg_org_id))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Aggregation consumer error | error={e}")
                await asyncio.sleep(5)

        logger.info("Aggregation consumer stopped")
