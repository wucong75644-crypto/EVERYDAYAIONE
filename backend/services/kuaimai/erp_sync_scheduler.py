"""
ERP 同步调度器

职责单一：定时扫描企业，判断哪些 (org_id, sync_type) 到期，
通过 ZADD NX 入 Redis Sorted Set 队列。不做同步执行。

score = timestamp - priority_weight，越小越先被 Worker 取出。
"""

import time
from datetime import datetime

from loguru import logger

from core.config import get_settings

# 同步类型优先级权重（越大 → score 越小 → 越先执行）
PRIORITY_WEIGHTS: dict[str, int] = {
    "order": 100,
    "aftersale": 100,
    "stock": 50,
    "product": 20,
    "supplier": 20,
    "purchase": 10,
    "receipt": 10,
    "shelf": 10,
    "purchase_return": 10,
    "platform_map": 0,
    "stock_full": 0,
    "daily_maintenance": 0,
    "order_reconcile": 0,
    "aftersale_reconcile": 0,
}

# 高频同步类型（每轮都入队）
HIGH_FREQ_TYPES = [
    "product", "stock", "supplier",
    "purchase", "receipt", "shelf",
    "purchase_return",
    "order", "aftersale",
]

# 低频同步类型
LOW_FREQ_TYPES = ["platform_map"]

# 特殊任务类型（按独立间隔调度）
SPECIAL_TYPES = ["stock_full", "daily_maintenance", "order_reconcile", "aftersale_reconcile"]


class ErpSyncScheduler:
    """定时扫描企业，产出同步任务到 Redis Sorted Set 队列。

    每轮调度周期（默认 60s）：
    1. 加载所有启用 ERP 的企业
    2. 对每个企业判断哪些 sync_type 到期
    3. ZADD NX 入队（原子去重，已存在则跳过）
    4. 检查队列积压并告警
    """

    # 日维护间隔（秒）：24小时
    DAILY_INTERVAL = 86400

    def __init__(self, db) -> None:
        self.db = db
        self.settings = get_settings()
        self.is_running = False
        self._first_round = True
        # 按企业隔离的上次执行时间戳
        self._org_last_platform_map: dict[str | None, datetime] = {}
        self._org_last_stock_full: dict[str | None, datetime] = {}
        self._org_last_daily: dict[str | None, datetime] = {}
        self._org_last_order_reconcile: dict[str | None, datetime] = {}
        self._org_last_aftersale_reconcile: dict[str | None, datetime] = {}

    async def start(self) -> None:
        """启动调度循环"""
        import asyncio
        self.is_running = True
        logger.info(
            f"ErpSyncScheduler started | interval={self.settings.erp_sync_interval}s"
        )

        while self.is_running:
            try:
                await self._schedule_round()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler round error | error={e}", exc_info=True)

            await asyncio.sleep(self.settings.erp_sync_interval)

        logger.info("ErpSyncScheduler stopped")

    async def stop(self) -> None:
        self.is_running = False

    async def _schedule_round(self) -> None:
        """一轮调度：扫描所有企业，入队到期任务。"""
        org_ids = await self._load_erp_org_ids()
        if not org_ids:
            return

        enqueued = 0
        for org_id in org_ids:
            due_types = self._get_due_types(org_id)
            for sync_type in due_types:
                if await self._enqueue_task(org_id, sync_type):
                    enqueued += 1

        if enqueued > 0:
            logger.debug(f"Scheduler enqueued | count={enqueued}")

        if self._first_round:
            self._first_round = False

        # 队列积压检查
        await self._check_queue_depth()

    def _get_due_types(self, org_id: str | None) -> list[str]:
        """判断该企业哪些 sync_type 到期需要入队。

        首轮启动只入队高频类型，低频/特殊任务延迟到第二轮，
        避免启动风暴（所有企业的所有任务一次性入队）。
        """
        due: list[str] = []

        # 高频类型：每轮都入队
        due.extend(HIGH_FREQ_TYPES)

        # 首轮只入队高频任务，低频/特殊任务延迟
        if self._first_round:
            return due

        # 低频类型：按间隔判断
        if self._is_interval_due(
            self._org_last_platform_map, org_id,
            self.settings.erp_platform_map_interval,
        ):
            due.extend(LOW_FREQ_TYPES)

        # 库存全量刷新
        if self._is_interval_due(
            self._org_last_stock_full, org_id,
            self.settings.erp_stock_full_refresh_interval,
        ):
            due.append("stock_full")

        # 日维护
        if self._is_interval_due(
            self._org_last_daily, org_id,
            self.DAILY_INTERVAL,
        ):
            due.append("daily_maintenance")

        # 订单+售后对账：仅在指定时点触发（默认凌晨3点），独立追踪
        reconcile_hour = self.settings.erp_reconcile_hour
        reconcile_interval = self.settings.erp_reconcile_interval
        if datetime.now().hour == reconcile_hour:
            if self._is_interval_due(
                self._org_last_order_reconcile, org_id, reconcile_interval,
            ):
                due.append("order_reconcile")
            if self._is_interval_due(
                self._org_last_aftersale_reconcile, org_id, reconcile_interval,
            ):
                due.append("aftersale_reconcile")

        return due

    @staticmethod
    def _is_interval_due(
        last_map: dict[str | None, datetime],
        org_id: str | None,
        interval_seconds: int,
    ) -> bool:
        """判断某个企业的某类任务是否到期。"""
        last = last_map.get(org_id)
        if last is None:
            return True
        return (datetime.now() - last).total_seconds() >= interval_seconds

    def mark_completed(self, org_id: str | None, sync_type: str) -> None:
        """Worker 完成任务后回调，更新调度时间戳。

        低频/特殊任务需要更新时间戳以控制下次调度时间。
        高频任务每轮都入队，不需要时间戳控制。
        """
        if sync_type == "platform_map":
            self._org_last_platform_map[org_id] = datetime.now()
        elif sync_type == "stock_full":
            self._org_last_stock_full[org_id] = datetime.now()
        elif sync_type == "daily_maintenance":
            self._org_last_daily[org_id] = datetime.now()
        elif sync_type == "order_reconcile":
            self._org_last_order_reconcile[org_id] = datetime.now()
        elif sync_type == "aftersale_reconcile":
            self._org_last_aftersale_reconcile[org_id] = datetime.now()

    async def _enqueue_task(self, org_id: str | None, sync_type: str) -> bool:
        """ZADD NX 入队，原子去重。"""
        from core.redis import RedisClient

        task_id = _build_task_id(org_id, sync_type)
        weight = PRIORITY_WEIGHTS.get(sync_type, 0)
        score = time.time() - weight

        return await RedisClient.enqueue_task(
            self.settings.erp_sync_queue_key, task_id, score,
        )

    async def _load_erp_org_ids(self) -> list[str | None]:
        """加载所有启用 ERP 的企业 org_id 列表。

        无企业时返回 [None] 代表散客模式。
        """
        try:
            result = await (
                self.db.table("organizations")
                .select("id, features")
                .eq("status", "active")
                .execute()
            )
            org_ids: list[str | None] = []
            for org in (result.data or []):
                features = org.get("features") or {}
                if features.get("erp"):
                    org_ids.append(str(org["id"]))

            if not org_ids:
                # 散客降级：检查全局凭证是否配置
                if self.settings.kuaimai_access_token:
                    return [None]
                return []

            return org_ids
        except Exception as e:
            logger.error(f"Failed to load ERP org IDs | error={e}")
            return []

    async def _check_queue_depth(self) -> None:
        """队列积压告警"""
        try:
            from core.redis import RedisClient
            depth = await RedisClient.queue_size(self.settings.erp_sync_queue_key)
            if depth > self.settings.erp_sync_worker_count * 20:
                logger.warning(f"ERP task queue backlog | depth={depth}")
        except Exception:
            pass


def _build_task_id(org_id: str | None, sync_type: str) -> str:
    """构造任务 ID：org_id:sync_type"""
    return f"{org_id or '__default__'}:{sync_type}"


def parse_task_id(task_id: str) -> tuple[str | None, str]:
    """解析任务 ID → (org_id, sync_type)"""
    parts = task_id.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid task_id: {task_id}")
    org_id_str, sync_type = parts
    org_id = None if org_id_str == "__default__" else org_id_str
    return org_id, sync_type
