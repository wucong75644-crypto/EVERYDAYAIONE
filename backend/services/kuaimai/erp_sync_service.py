"""
ERP 数据本地索引同步核心框架

提供状态管理、增量时间窗口、窗口分片、翻页拉取等基础设施。
数据持久化（upsert/聚合/排序）委托给 erp_sync_persistence 模块。

设计文档: docs/document/TECH_ERP数据本地索引系统.md
"""

import asyncio
from datetime import datetime, timedelta
from functools import partial
from collections.abc import AsyncGenerator
from typing import Any

from loguru import logger

from core.config import get_settings
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.erp_sync_handlers import (
    sync_aftersale,
    sync_order,
    sync_purchase,
    sync_purchase_return,
    sync_receipt,
    sync_shelf,
)
from services.kuaimai.erp_sync_master_handlers import (
    sync_platform_map,
    sync_product,
    sync_stock,
    sync_stock_full,
    sync_supplier,
)
from services.kuaimai.erp_sync_config_handlers import (
    sync_category,
    sync_logistics_company,
    sync_shop,
    sync_tag,
    sync_warehouse,
)
from services.kuaimai.erp_sync_warehouse_handlers import (
    sync_allocate,
    sync_allocate_in,
    sync_allocate_out,
    sync_inventory_sheet,
    sync_other_in,
    sync_other_out,
    sync_process_order,
    sync_section_record,
    sync_unshelve,
)
from services.kuaimai.erp_sync_piggyback_handlers import (
    sync_batch_stock,
    sync_goods_section,
)
from services.kuaimai.erp_sync_persistence import (
    collect_affected_keys as _collect_affected_keys,
    run_aggregation as _run_aggregation,
    sort_and_assign_index as _sort_and_assign_index,
    upsert_document_items as _upsert_document_items,
)
from services.kuaimai.erp_sync_utils import _API_SEM


class ErpSyncService:
    """ERP 同步核心服务"""

    # 各类型首次同步天数覆盖（订单/售后只需近90天，其他走全局 erp_sync_initial_days）
    INITIAL_DAYS_OVERRIDE: dict[str, int] = {
        "order": 90,
        "aftersale": 90,
        "platform_map": 1,  # 不按时间分片，跑一遍即可
        # 全量类型（handler 内忽略 start/end，跑一遍标记 done）
        "shop": 1,
        "warehouse": 1,
        "tag": 1,
        "category": 1,
        "logistics_company": 1,
    }

    FLUSH_THRESHOLD = 1000  # 每积累N条写一次库，控制内存峰值

    def __init__(
        self, db, lock_extend_fn=None,
        aggregation_queue: asyncio.Queue | None = None,
        aggregation_pending: set[tuple[str, str]] | None = None,
        org_id: str | None = None,
        client: KuaiMaiClient | None = None,
    ) -> None:
        self.db = db
        self.settings = get_settings()
        self.org_id = org_id
        self._client: KuaiMaiClient | None = client
        self._lock_extend_fn = lock_extend_fn
        self._aggregation_queue = aggregation_queue
        self._aggregation_pending = aggregation_pending

    def _get_client(self) -> KuaiMaiClient:
        if self._client is None:
            if self.org_id:
                logger.warning(
                    f"ErpSyncService._get_client fallback to global credentials | "
                    f"org_id={self.org_id} — should have been passed a client"
                )
            self._client = KuaiMaiClient()
        return self._client

    # ── 主入口 ────────────────────────────────────────────

    async def sync(self, sync_type: str) -> None:
        """执行指定类型的增量同步"""
        state = await self._get_sync_state(sync_type)
        if state is None:
            await self._init_sync_state(sync_type)
            state = await self._get_sync_state(sync_type)
            if state is None:
                logger.error(f"Failed to init sync state | type={sync_type}")
                return

        # 首次全量未完成 → 走全量逻辑（分片拉取 + 断点续传）
        if not state.get("is_initial_done", False):
            await self._run_initial_sync(sync_type, state)
            return

        try:
            windows = self._calculate_time_windows(state)
            total_synced = 0

            for start, end in windows:
                count = await self._sync_window(sync_type, start, end)
                total_synced += count
                # 每片完成后更新 last_sync_time（断点续传）
                await self._update_sync_state_progress(sync_type, end)

            await self._update_sync_state_success(sync_type, total_synced)
            if total_synced > 0:
                logger.info(f"ERP sync done | type={sync_type} | synced={total_synced}")
        except Exception as e:
            await self._update_sync_state_error(sync_type, str(e))
            raise

    # 初始同步并发分片数（API 限流由全局 _API_SEM 12QPS 保护）
    INITIAL_SYNC_CONCURRENCY = 3

    # 单个分片最大重试次数
    SHARD_MAX_RETRIES = 3
    # 分片重试间隔（秒）
    SHARD_RETRY_DELAY = 10

    async def _run_initial_sync(self, sync_type: str, state: dict[str, Any]) -> None:
        """
        首次全量同步（设计文档 §7.3）

        分片并发拉取（INITIAL_SYNC_CONCURRENCY 路），加速初始同步。
        每个分片失败后自动重试（最多 SHARD_MAX_RETRIES 次），
        全部分片成功才标记 is_initial_done=True。
        """
        try:
            windows = self._calculate_time_windows(state)
            total_shards = len(windows)
            total_synced = 0
            sem = asyncio.Semaphore(self.INITIAL_SYNC_CONCURRENCY)

            logger.info(
                f"ERP initial sync start | type={sync_type} | "
                f"shards={total_shards} | concurrency={self.INITIAL_SYNC_CONCURRENCY}"
            )

            async def _run_shard_with_retry(
                idx: int, start: datetime, end: datetime,
            ) -> int:
                async with sem:
                    last_error: Exception | None = None
                    for attempt in range(1, self.SHARD_MAX_RETRIES + 1):
                        try:
                            count = await self._sync_window(sync_type, start, end)
                            if self._lock_extend_fn:
                                await self._lock_extend_fn()
                            if attempt > 1:
                                logger.info(
                                    f"ERP initial sync shard recovered | "
                                    f"type={sync_type} | shard={idx}/{total_shards} | "
                                    f"attempt={attempt} | synced={count}"
                                )
                            else:
                                logger.info(
                                    f"ERP initial sync | type={sync_type} | "
                                    f"shard={idx}/{total_shards} | synced={count}"
                                )
                            return count
                        except Exception as e:
                            last_error = e
                            if attempt < self.SHARD_MAX_RETRIES:
                                logger.warning(
                                    f"ERP initial sync shard retry | "
                                    f"type={sync_type} | shard={idx}/{total_shards} | "
                                    f"attempt={attempt}/{self.SHARD_MAX_RETRIES} | "
                                    f"error={e}"
                                )
                                await asyncio.sleep(
                                    self.SHARD_RETRY_DELAY * attempt
                                )
                    # 重试耗尽，抛出异常让 gather 捕获
                    raise last_error  # type: ignore[misc]

            tasks = [
                _run_shard_with_retry(idx, start, end)
                for idx, (start, end) in enumerate(windows, 1)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            failed_shards = 0
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    failed_shards += 1
                    logger.error(
                        f"ERP initial sync shard failed | type={sync_type} | "
                        f"shard={idx + 1}/{total_shards} | error={result}"
                    )
                else:
                    total_synced += result

            # 更新断点到最后一个窗口的结束时间
            if windows:
                await self._update_sync_state_progress(sync_type, windows[-1][1])

            # 有分片失败 → 不标记完成，下次启动重新全量
            if failed_shards > 0:
                logger.error(
                    f"ERP initial sync incomplete | type={sync_type} | "
                    f"failed_shards={failed_shards}/{total_shards} | "
                    f"synced={total_synced} | will retry on next round"
                )
                return

            # 全量完成（零失败）→ 标记切换增量模式
            await self._mark_initial_done(sync_type, total_synced)
            logger.info(
                f"ERP initial sync done | type={sync_type} | "
                f"total={total_synced}"
            )
        except Exception as e:
            await self._update_sync_state_error(sync_type, str(e))
            raise

    async def _sync_window(self, sync_type: str, start: datetime, end: datetime) -> int:
        """同步单个时间窗口（子类按类型实现，阶段2补充）"""
        # 阶段2各同步器将覆盖此分发逻辑
        handler = self._get_sync_handler(sync_type)
        if handler is None:
            logger.debug(f"ERP sync handler not implemented | type={sync_type}")
            return 0
        return await handler(start, end)

    def _get_sync_handler(self, sync_type: str):
        """获取同步类型对应的处理方法"""
        handlers = {
            "purchase": partial(sync_purchase, self),
            "receipt": partial(sync_receipt, self),
            "shelf": partial(sync_shelf, self),
            "purchase_return": partial(sync_purchase_return, self),
            "aftersale": partial(sync_aftersale, self),
            "order": partial(sync_order, self),
            "product": partial(sync_product, self),
            "stock": partial(sync_stock, self),
            "supplier": partial(sync_supplier, self),
            "platform_map": partial(sync_platform_map, self),
            "shop": partial(sync_shop, self),
            "warehouse": partial(sync_warehouse, self),
            "tag": partial(sync_tag, self),
            "category": partial(sync_category, self),
            "logistics_company": partial(sync_logistics_company, self),
            "allocate": partial(sync_allocate, self),
            "allocate_in": partial(sync_allocate_in, self),
            "allocate_out": partial(sync_allocate_out, self),
            "other_in": partial(sync_other_in, self),
            "other_out": partial(sync_other_out, self),
            "inventory_sheet": partial(sync_inventory_sheet, self),
            "unshelve": partial(sync_unshelve, self),
            "process_order": partial(sync_process_order, self),
            "section_record": partial(sync_section_record, self),
            "goods_section": partial(sync_goods_section, self),
        }
        return handlers.get(sync_type)

    # ── 状态管理 ──────────────────────────────────────────


    async def _get_sync_state(self, sync_type: str) -> dict[str, Any] | None:
        """读取同步状态"""
        try:
            q = self.db.table("erp_sync_state").select("*").eq("sync_type", sync_type)
            result = await q.execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to read sync state | type={sync_type} | error={e}")
            return None

    async def _init_sync_state(self, sync_type: str) -> None:
        """初始化同步状态行"""
        try:
            await self.db.table("erp_sync_state").insert({
                "sync_type": sync_type,
                "org_id": self.org_id,
                "status": "idle",
                "is_initial_done": False,
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to init sync state | type={sync_type} | error={e}")

    async def _update_sync_state_success(self, sync_type: str, synced_count: int) -> None:
        """同步成功后更新状态"""
        try:
            q = self.db.table("erp_sync_state").select("total_synced").eq("sync_type", sync_type)
            current = await q.execute()
            new_total = (current.data[0]["total_synced"] or 0) + synced_count
            uq = self.db.table("erp_sync_state").update({
                "status": "idle",
                "last_run_at": datetime.now().isoformat(),
                "error_count": 0,
                "last_error": None,
                "total_synced": new_total,
            }).eq("sync_type", sync_type)
            await uq.execute()
        except Exception as e:
            logger.error(f"Failed to update sync state | type={sync_type} | error={e}")

    ALERT_ERROR_THRESHOLD = 5  # 连续失败次数告警阈值

    async def _update_sync_state_error(self, sync_type: str, error_msg: str) -> None:
        """同步失败后更新状态"""
        try:
            state = await self._get_sync_state(sync_type)
            error_count = (state.get("error_count", 0) + 1) if state else 1
            uq = self.db.table("erp_sync_state").update({
                "status": "error",
                "last_run_at": datetime.now().isoformat(),
                "error_count": error_count,
                "last_error": error_msg[:500],
            }).eq("sync_type", sync_type)
            await uq.execute()

            if error_count >= self.ALERT_ERROR_THRESHOLD:
                logger.error(
                    f"ERP sync repeated failure | type={sync_type} | "
                    f"consecutive_errors={error_count} | last_error={error_msg[:200]}"
                )
        except Exception as e:
            logger.error(f"Failed to update error state | type={sync_type} | error={e}")

    async def _update_sync_state_progress(self, sync_type: str, time_point: datetime) -> None:
        """分片同步中更新进度（断点续传）"""
        try:
            uq = self.db.table("erp_sync_state").update({
                "last_sync_time": time_point.isoformat(),
            }).eq("sync_type", sync_type)
            await uq.execute()
        except Exception as e:
            logger.error(f"Failed to update progress | type={sync_type} | error={e}")

    async def _mark_initial_done(self, sync_type: str, total_synced: int) -> None:
        """全量同步完成后标记切换增量模式"""
        try:
            uq = self.db.table("erp_sync_state").update({
                "is_initial_done": True,
                "status": "idle",
                "last_run_at": datetime.now().isoformat(),
                "error_count": 0,
                "last_error": None,
                "total_synced": total_synced,
            }).eq("sync_type", sync_type)
            await uq.execute()
        except Exception as e:
            logger.error(f"Failed to mark initial done | type={sync_type} | error={e}")

    # ── 时间窗口 ──────────────────────────────────────────

    def _calculate_time_windows(
        self, state: dict[str, Any]
    ) -> list[tuple[datetime, datetime]]:
        """
        计算增量时间窗口（含自动分片）

        设计文档 §7.2：窗口 > 7天自动按 shard_days 切分。
        回溯策略：单据类型5分钟，商品/库存类型1天。
        """
        now = datetime.now()
        sync_type = state["sync_type"]
        last_sync = state.get("last_sync_time")

        if last_sync is None:
            # 无历史记录，按类型取首次同步天数
            initial_days = self.INITIAL_DAYS_OVERRIDE.get(
                sync_type, self.settings.erp_sync_initial_days
            )
            start = now - timedelta(days=initial_days)
        elif isinstance(last_sync, str):
            parsed = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            # 去掉时区信息（DB 存的是北京时间 naive datetime）
            start = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        else:
            # datetime 对象：去掉时区信息
            start = last_sync.replace(tzinfo=None) if last_sync.tzinfo else last_sync

        # 回溯策略：商品/库存/供应商日期精度到天，多回溯1天
        if sync_type in ("product", "stock", "platform_map"):
            start = start - timedelta(days=1)
        else:
            start = start - timedelta(minutes=5)

        # 自动分片（设计文档 BUG-5）
        shard_days = self.settings.erp_sync_shard_days
        total_span = (now - start).total_seconds() / 86400

        if total_span <= shard_days:
            return [(start, now)]

        windows = []
        cursor = start
        while cursor < now:
            shard_end = min(cursor + timedelta(days=shard_days), now)
            windows.append((cursor, shard_end))
            cursor = shard_end

        logger.info(
            f"ERP sync sharded | type={sync_type} | "
            f"total_days={total_span:.1f} | shards={len(windows)}"
        )
        return windows

    # ── 翻页拉取 ──────────────────────────────────────────

    MAX_PAGES = 500  # 页数上限，防止异常数据导致无限循环

    async def fetch_all_pages(
        self,
        method: str,
        params: dict[str, Any],
        response_key: str = "list",
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """翻页拉取全部数据

        终止判断：返回条数 < 请求 pageSize 或返回空列表。
        限速：通过全局 _API_SEM 信号量控制并发（类型并行时共享限流）。
        """
        client = self._get_client()
        all_items: list[dict[str, Any]] = []
        page = 0

        while page < self.MAX_PAGES:
            page += 1
            params["pageNo"] = page
            params["pageSize"] = page_size
            async with _API_SEM:
                data = await client.request_with_retry(method, params)
            items = data.get(response_key) or []
            all_items.extend(items)

            # 终止：返回条数 < 请求的 pageSize，说明已到最后一页
            if len(items) < page_size:
                break
        else:
            logger.warning(
                f"fetch_all_pages hit limit | method={method} | "
                f"pages={page} | total_items={len(all_items)}"
            )

        if all_items:
            logger.info(
                f"fetch_all_pages done | method={method} | "
                f"pages={page} | total={len(all_items)}"
            )
        else:
            logger.debug(
                f"fetch_all_pages empty | method={method} | "
                f"pages={page} | params={params}"
            )

        return all_items

    async def fetch_pages_streaming(
        self,
        method: str,
        params: dict[str, Any],
        response_key: str = "list",
        page_size: int = 50,
    ) -> AsyncGenerator[list[dict[str, Any]], None]:
        """逐页拉取数据（流式），每次 yield 一页，避免全量堆积在内存

        与 fetch_all_pages 相同的翻页/限流/终止逻辑，
        但每页拉完立刻 yield 给调用方处理，内存峰值 = 1页数据。
        """
        client = self._get_client()
        page = 0
        total = 0

        while page < self.MAX_PAGES:
            page += 1
            params["pageNo"] = page
            params["pageSize"] = page_size
            async with _API_SEM:
                data = await client.request_with_retry(method, params)
            items = data.get(response_key) or []
            total += len(items)

            if items:
                yield items

            if len(items) < page_size:
                break
        else:
            logger.warning(
                f"fetch_pages_streaming hit limit | method={method} | "
                f"pages={page} | total_items={total}"
            )

        if total > 0:
            logger.info(
                f"fetch_all_pages done | method={method} | "
                f"pages={page} | total={total}"
            )
        else:
            logger.debug(
                f"fetch_all_pages empty | method={method} | "
                f"pages={page} | params={params}"
            )

    # ── 委托到 erp_sync_persistence ─────────────────────

    @classmethod
    def sort_and_assign_index(
        cls, items: list[dict[str, Any]], sync_type: str,
    ) -> list[dict[str, Any]]:
        """按确定性字段排序后分配顺序 item_index"""
        return _sort_and_assign_index(items, sync_type)

    async def upsert_document_items(self, rows: list[dict[str, Any]]) -> int:
        """事务性写入 erp_document_items（按单据分组：删旧→插新）"""
        return await _upsert_document_items(self.db, rows, org_id=self.org_id)

    async def run_aggregation(self, affected_keys: list[tuple[str, str]]) -> None:
        """将受影响的 (outer_id, stat_date) 推入聚合队列"""
        return _run_aggregation(
            self.db, self._aggregation_queue, affected_keys,
            pending=self._aggregation_pending,
            org_id=self.org_id,
        )

    def collect_affected_keys(self, rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """从入库行中收集受影响的 (outer_id, stat_date) 对"""
        return _collect_affected_keys(rows)
