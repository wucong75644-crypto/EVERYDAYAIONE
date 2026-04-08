"""
ERP 同步执行器（纯执行逻辑）

从 ErpSyncWorker 中提取的日维护、归档、聚合兜底、删除检测逻辑。
不包含调度、锁管理、队列消费代码。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from loguru import logger

from core.config import get_settings

# 删除检测间隔（秒）：7天
DELETION_INTERVAL = 604800


class ErpSyncExecutor:
    """ERP 同步纯执行逻辑（无调度、无锁管理）"""

    def __init__(
        self,
        db,
        aggregation_queue: asyncio.Queue | None = None,
        aggregation_pending: set | None = None,
    ) -> None:
        self.db = db
        self.settings = get_settings()
        self._aggregation_queue = aggregation_queue
        self._aggregation_pending = aggregation_pending
        # 删除检测时间戳（按企业隔离）
        self._org_last_deletion: dict[str | None, datetime] = {}

    # ── 日维护 ─────────────────────────────────────────

    async def run_daily_maintenance(
        self, org_id: str | None = None, client=None,
    ) -> None:
        """执行日维护：归档 → 聚合兜底 → 删除检测（按企业隔离）"""
        logger.info(f"ERP daily maintenance started | org_id={org_id}")
        archived, reagg, deleted = 0, 0, 0

        try:
            archived = await self._run_archive(org_id=org_id)
        except Exception as e:
            logger.error(
                f"Daily maintenance: archive failed | org_id={org_id} | error={e}",
                exc_info=True,
            )

        try:
            reagg = await self._run_daily_reaggregation(org_id=org_id)
        except Exception as e:
            logger.error(
                f"Daily maintenance: reaggregation failed | org_id={org_id} | error={e}",
                exc_info=True,
            )

        try:
            if self._should_run_deletion(org_id):
                deleted = await self._run_deletion_detection(
                    org_id=org_id, client=client,
                )
                self._org_last_deletion[org_id] = datetime.now()
        except Exception as e:
            logger.error(
                f"Daily maintenance: deletion detection failed | org_id={org_id} | error={e}",
                exc_info=True,
            )

        logger.info(
            f"ERP daily maintenance done | org_id={org_id} | archived={archived} | "
            f"reaggregated={reagg} | deleted={deleted}"
        )

    def _should_run_deletion(self, org_id: str | None = None) -> bool:
        last = self._org_last_deletion.get(org_id)
        if last is None:
            return True
        return (datetime.now() - last).total_seconds() >= DELETION_INTERVAL

    # ── 归档 ───────────────────────────────────────────

    async def _run_archive(self, org_id: str | None = None) -> int:
        """热表→冷表归档：分批 SELECT→UPSERT(archive)→DELETE(hot)

        归档条件：doc_modified_at 和 synced_at 都超过保留期才归档。
        synced_at 保底：防止 modified 为 ERP 零值（如 2000-01-01）的
        补发单/手工单被误归档。
        """
        from services.kuaimai.erp_local_helpers import _apply_org

        cutoff = (
            datetime.now() - timedelta(days=self.settings.erp_archive_retention_days)
        ).isoformat()

        total_archived = 0
        batch_size = 1000

        while True:
            try:
                q = (
                    self.db.table("erp_document_items")
                    .select("*")
                    .lt("doc_modified_at", cutoff)
                    .lt("synced_at", cutoff)
                )
                result = await _apply_org(q, org_id).limit(batch_size).execute()
                rows = result.data or []
                if not rows:
                    break

                await self.db.table("erp_document_items_archive").upsert(
                    rows, on_conflict="doc_type,doc_id,item_index",
                ).execute()

                ids = [r["id"] for r in rows]
                await self.db.table("erp_document_items").delete().in_(
                    "id", ids,
                ).execute()

                total_archived += len(rows)
            except Exception as e:
                logger.error(f"Archive batch failed | error={e}")
                break

        return total_archived

    # ── 聚合兜底 ───────────────────────────────────────

    async def _run_daily_reaggregation(self, org_id: str | None = None) -> int:
        """每日聚合兜底：对近7天数据重新聚合"""
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            result = await self.db.rpc(
                "erp_aggregate_daily_stats_batch",
                {"p_since_date": cutoff, "p_org_id": org_id},
            ).execute()
            count = result.data if isinstance(result.data, int) else 0
            return count
        except Exception as e:
            logger.warning(f"Batch reaggregation unavailable, fallback | error={e}")
            return await self._reaggregate_fallback(cutoff, org_id=org_id)

    async def _reaggregate_fallback(
        self, since_date: str, org_id: str | None = None,
    ) -> int:
        """逐条降级聚合"""
        try:
            from core.org_scoped_db import OrgScopedDB
            from services.kuaimai.erp_sync_service import ErpSyncService
            from services.kuaimai.erp_local_helpers import _apply_org

            scoped_db = OrgScopedDB(self.db, org_id)
            svc = ErpSyncService(
                scoped_db,
                aggregation_queue=self._aggregation_queue,
                aggregation_pending=self._aggregation_pending,
                org_id=org_id,
            )

            q = (
                self.db.table("erp_document_items")
                .select("outer_id,doc_created_at")
                .gte("doc_created_at", since_date)
                .not_.is_("outer_id", "null")
            )
            result = await _apply_org(q, org_id).execute()
            rows = result.data or []
            keys = svc.collect_affected_keys(rows)
            await svc.run_aggregation(keys)
            return len(keys)
        except Exception as e:
            logger.error(f"Fallback reaggregation failed | error={e}")
            return 0

    # ── 删除检测 ───────────────────────────────────────

    async def _run_deletion_detection(
        self, org_id: str | None = None, client=None,
    ) -> int:
        """商品删除检测（SPU + SKU 级别，按企业隔离）"""
        try:
            from core.org_scoped_db import OrgScopedDB
            from services.kuaimai.erp_sync_service import ErpSyncService

            scoped_db = OrgScopedDB(self.db, org_id)
            svc = ErpSyncService(scoped_db, org_id=org_id, client=client)
            products = await svc.fetch_all_pages(
                "item.list.query",
                {
                    "startModified": "2020-01-01 00:00:00",
                    "endModified": "2099-12-31 23:59:59",
                },
                response_key="items",
                page_size=100,
            )

            api_spu_ids, api_sku_ids = self._collect_api_product_ids(products)
            count = 0

            # SPU 删除检测
            db_spu_ids = await self._paginated_select_ids(
                "erp_products", "outer_id", org_id=org_id,
            )
            deleted_spus = db_spu_ids - api_spu_ids
            count += await self._mark_deleted_items(
                "erp_products", "outer_id", deleted_spus, org_id=org_id,
            )
            if deleted_spus:
                logger.info(
                    f"SPU deletion detected | org_id={org_id} | count={len(deleted_spus)}"
                )

            # SKU 删除检测
            db_sku_ids = await self._paginated_select_ids(
                "erp_product_skus", "sku_outer_id", org_id=org_id,
            )
            deleted_skus = db_sku_ids - api_sku_ids
            count += await self._mark_deleted_items(
                "erp_product_skus", "sku_outer_id", deleted_skus, org_id=org_id,
            )
            if deleted_skus:
                logger.info(f"SKU deletion detected | count={len(deleted_skus)}")

            return count
        except Exception as e:
            logger.error(f"Deletion detection failed | error={e}")
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
        """分页加载表中所有活跃记录的指定列"""
        from services.kuaimai.erp_local_helpers import _apply_org

        ids: set[str] = set()
        offset = 0
        while True:
            q = self.db.table(table).select(id_column).neq("active_status", -1)
            r = await _apply_org(q, org_id).range(
                offset, offset + batch_size - 1,
            ).execute()
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
        """批量标记已删除的 SPU/SKU"""
        from services.kuaimai.erp_local_helpers import _apply_org

        count = 0
        for item_id in deleted_ids:
            try:
                q = self.db.table(table).update({
                    "active_status": -1,
                }).eq(id_col, item_id)
                await _apply_org(q, org_id).execute()
                count += 1
            except Exception as e:
                logger.warning(
                    f"Mark deleted failed | {id_col}={item_id} | error={e}"
                )
        return count

    # ── 对账 ──────────────────────────────────────────────

    async def run_order_reconcile(
        self, org_id: str | None = None, client=None,
        lock_extend_fn=None,
    ) -> int:
        """执行订单分层对账"""
        from core.org_scoped_db import OrgScopedDB
        from services.kuaimai.erp_sync_reconcile import reconcile_order
        from services.kuaimai.erp_sync_service import ErpSyncService

        scoped_db = OrgScopedDB(self.db, org_id)
        svc = ErpSyncService(
            scoped_db, lock_extend_fn=lock_extend_fn,
            org_id=org_id, client=client,
        )
        return await reconcile_order(svc)

    async def run_aftersale_reconcile(
        self, org_id: str | None = None, client=None,
        lock_extend_fn=None,
    ) -> int:
        """执行售后分层对账"""
        from core.org_scoped_db import OrgScopedDB
        from services.kuaimai.erp_sync_reconcile import reconcile_aftersale
        from services.kuaimai.erp_sync_service import ErpSyncService

        scoped_db = OrgScopedDB(self.db, org_id)
        svc = ErpSyncService(
            scoped_db, lock_extend_fn=lock_extend_fn,
            org_id=org_id, client=client,
        )
        return await reconcile_aftersale(svc)
