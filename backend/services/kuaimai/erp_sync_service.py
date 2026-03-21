"""
ERP 数据本地索引同步核心框架

提供状态管理、增量时间窗口、窗口分片、翻页拉取、
item_index 稳定排序、upsert 入库、聚合计算等基础设施。

各 sync_type 的具体实现（字段映射/解析逻辑）在阶段2任务中补充。

设计文档: docs/document/TECH_ERP数据本地索引系统.md
"""

from datetime import datetime, timedelta, timezone
from functools import partial
from collections.abc import AsyncGenerator
from typing import Any

from loguru import logger
from supabase import Client

from core.config import get_settings
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.erp_sync_handlers import (
    _API_SEM,
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
    sync_supplier,
)


class ErpSyncService:
    """ERP 同步核心服务"""

    # item_index 排序键（设计文档 BUG-2：保证跨调用稳定）
    ITEM_SORT_KEYS: dict[str, list[str]] = {
        "order": ["oid"],
        "aftersale": ["mainOuterId", "outerId"],
        "purchase": ["outerId", "itemOuterId"],
        "purchase_return": ["outerId", "itemOuterId"],
        "receipt": ["outerId", "itemOuterId"],
        "shelf": ["outerId", "itemOuterId"],
    }

    FLUSH_THRESHOLD = 1000  # 每积累N条写一次库，控制内存峰值

    def __init__(self, db: Client, lock_extend_fn=None) -> None:
        self.db = db
        self.settings = get_settings()
        self._client: KuaiMaiClient | None = None
        self._lock_extend_fn = lock_extend_fn

    def _get_client(self) -> KuaiMaiClient:
        if self._client is None:
            self._client = KuaiMaiClient()
        return self._client

    # ── 主入口 ────────────────────────────────────────────

    async def sync(self, sync_type: str) -> None:
        """执行指定类型的增量同步"""
        state = self._get_sync_state(sync_type)
        if state is None:
            self._init_sync_state(sync_type)
            state = self._get_sync_state(sync_type)

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
                self._update_sync_state_progress(sync_type, end)

            self._update_sync_state_success(sync_type, total_synced)
            if total_synced > 0:
                logger.info(f"ERP sync done | type={sync_type} | synced={total_synced}")
        except Exception as e:
            self._update_sync_state_error(sync_type, str(e))
            raise

    async def _run_initial_sync(self, sync_type: str, state: dict[str, Any]) -> None:
        """
        首次全量同步（设计文档 §7.3）

        复用 _calculate_time_windows 分片 + _sync_window 拉取。
        每片完成后更新 last_sync_time（断点续传），全部完成设 is_initial_done=True。
        """
        try:
            windows = self._calculate_time_windows(state)
            total_synced = 0
            total_shards = len(windows)

            logger.info(
                f"ERP initial sync start | type={sync_type} | "
                f"shards={total_shards}"
            )

            for idx, (start, end) in enumerate(windows, 1):
                count = await self._sync_window(sync_type, start, end)
                total_synced += count
                self._update_sync_state_progress(sync_type, end)
                # 每个 shard 后续期锁，防止长时间全量同步导致锁过期
                if self._lock_extend_fn:
                    await self._lock_extend_fn()
                logger.info(
                    f"ERP initial sync | type={sync_type} | "
                    f"shard={idx}/{total_shards} | synced={count}"
                )

            # 全量完成 → 标记切换增量模式
            self._mark_initial_done(sync_type, total_synced)
            logger.info(
                f"ERP initial sync done | type={sync_type} | "
                f"total={total_synced}"
            )
        except Exception as e:
            self._update_sync_state_error(sync_type, str(e))
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
        }
        return handlers.get(sync_type)

    # ── 状态管理 ──────────────────────────────────────────

    def _get_sync_state(self, sync_type: str) -> dict[str, Any] | None:
        """读取同步状态"""
        try:
            result = (
                self.db.table("erp_sync_state")
                .select("*")
                .eq("sync_type", sync_type)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to read sync state | type={sync_type} | error={e}")
            return None

    def _init_sync_state(self, sync_type: str) -> None:
        """初始化同步状态行"""
        try:
            self.db.table("erp_sync_state").insert({
                "sync_type": sync_type,
                "status": "idle",
                "is_initial_done": False,
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to init sync state | type={sync_type} | error={e}")

    def _update_sync_state_success(self, sync_type: str, synced_count: int) -> None:
        """同步成功后更新状态"""
        try:
            self.db.table("erp_sync_state").update({
                "status": "idle",
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "error_count": 0,
                "last_error": None,
                "total_synced": self.db.table("erp_sync_state")
                    .select("total_synced")
                    .eq("sync_type", sync_type)
                    .execute().data[0]["total_synced"] + synced_count,
            }).eq("sync_type", sync_type).execute()
        except Exception as e:
            logger.error(f"Failed to update sync state | type={sync_type} | error={e}")

    ALERT_ERROR_THRESHOLD = 5  # 连续失败次数告警阈值

    def _update_sync_state_error(self, sync_type: str, error_msg: str) -> None:
        """同步失败后更新状态"""
        try:
            state = self._get_sync_state(sync_type)
            error_count = (state.get("error_count", 0) + 1) if state else 1
            self.db.table("erp_sync_state").update({
                "status": "error",
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "error_count": error_count,
                "last_error": error_msg[:500],
            }).eq("sync_type", sync_type).execute()

            if error_count >= self.ALERT_ERROR_THRESHOLD:
                logger.error(
                    f"ERP sync repeated failure | type={sync_type} | "
                    f"consecutive_errors={error_count} | last_error={error_msg[:200]}"
                )
        except Exception as e:
            logger.error(f"Failed to update error state | type={sync_type} | error={e}")

    def _update_sync_state_progress(self, sync_type: str, time_point: datetime) -> None:
        """分片同步中更新进度（断点续传）"""
        try:
            self.db.table("erp_sync_state").update({
                "last_sync_time": time_point.isoformat(),
            }).eq("sync_type", sync_type).execute()
        except Exception as e:
            logger.error(f"Failed to update progress | type={sync_type} | error={e}")

    def _mark_initial_done(self, sync_type: str, total_synced: int) -> None:
        """全量同步完成后标记切换增量模式"""
        try:
            self.db.table("erp_sync_state").update({
                "is_initial_done": True,
                "status": "idle",
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "error_count": 0,
                "last_error": None,
                "total_synced": total_synced,
            }).eq("sync_type", sync_type).execute()
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
        now = datetime.now(timezone.utc)
        sync_type = state["sync_type"]
        last_sync = state.get("last_sync_time")

        if last_sync is None:
            # 无历史记录，从 initial_days 前开始
            start = now - timedelta(days=self.settings.erp_sync_initial_days)
        elif isinstance(last_sync, str):
            parsed = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            # 确保 timezone-aware（DB 可能返回不带时区的字符串）
            start = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        else:
            # datetime 对象：确保 timezone-aware
            start = last_sync if last_sync.tzinfo else last_sync.replace(tzinfo=timezone.utc)

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

    # ── item_index 稳定排序 ───────────────────────────────

    @classmethod
    def sort_and_assign_index(
        cls, items: list[dict[str, Any]], sync_type: str
    ) -> list[dict[str, Any]]:
        """
        按确定性字段排序后分配 item_index（设计文档 BUG-2）

        API 返回的子项数组排序不保证跨调用稳定。
        排序后再分配 item_index 保证同一单据多次同步产生一致结果。
        """
        sort_keys = cls.ITEM_SORT_KEYS.get(sync_type, ["outerId", "itemOuterId"])

        def sort_key(item: dict) -> tuple:
            return tuple(str(item.get(k, "")) for k in sort_keys)

        sorted_items = sorted(items, key=sort_key)
        for idx, item in enumerate(sorted_items):
            item["_item_index"] = idx
        return sorted_items

    # ── 数据入库 ──────────────────────────────────────────

    def upsert_document_items(self, rows: list[dict[str, Any]]) -> int:
        """
        批量 upsert 到 erp_document_items

        Returns:
            成功写入/更新的行数
        """
        if not rows:
            return 0

        # 分批提交（每批100条，避免单次 payload 过大）
        batch_size = 100
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            try:
                self.db.table("erp_document_items").upsert(
                    batch,
                    on_conflict="doc_type,doc_id,item_index",
                ).execute()
                total += len(batch)
            except Exception as e:
                logger.error(
                    f"Upsert failed | batch={i // batch_size} | "
                    f"rows={len(batch)} | error={e}"
                )
        return total

    # ── 聚合计算 ──────────────────────────────────────────

    def run_aggregation(
        self,
        affected_keys: list[tuple[str, str]],
    ) -> None:
        """
        对受影响的 (outer_id, stat_date) 重算 daily_stats

        聚合规则（设计文档 G1）：
        - *_count = COUNT(DISTINCT doc_id)，按单据去重
        - *_qty = SUM(quantity)
        - *_amount = SUM(amount)

        Args:
            affected_keys: [(outer_id, stat_date_str), ...]
        """
        if not affected_keys:
            return

        for outer_id, stat_date in affected_keys:
            try:
                self.db.rpc(
                    "erp_aggregate_daily_stats",
                    {"p_outer_id": outer_id, "p_stat_date": stat_date},
                ).execute()
            except Exception as e:
                logger.error(
                    f"Aggregation failed | outer_id={outer_id} | "
                    f"date={stat_date} | error={e}"
                )

    def collect_affected_keys(
        self, rows: list[dict[str, Any]]
    ) -> list[tuple[str, str]]:
        """从入库行中收集受影响的 (outer_id, stat_date) 对"""
        seen: set[tuple[str, str]] = set()
        for row in rows:
            outer_id = row.get("outer_id")
            created_at = row.get("doc_created_at")
            if outer_id and created_at:
                # 提取日期部分
                if isinstance(created_at, str):
                    stat_date = created_at[:10]
                elif isinstance(created_at, datetime):
                    stat_date = created_at.strftime("%Y-%m-%d")
                else:
                    continue
                seen.add((outer_id, stat_date))
        return list(seen)
