"""ERP 库存同步：增量（时间查询直接 upsert）+ 全量（活跃商品全扫）"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import (
    _batch_upsert,
    _fmt_dt,
    _ms_to_iso,
    _pick,
)

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


def _map_stock_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """API 库存记录 → DB 行映射（增量/全量共用）"""
    outer_id = item.get("mainOuterId") or item.get("outerId")
    if not outer_id:
        return None
    return {
        "outer_id": outer_id,
        "sku_outer_id": item.get("skuOuterId") or "",
        "item_name": item.get("title"),
        "properties_name": item.get("propertiesName"),
        "total_stock": item.get("totalAvailableStockSum", 0),
        "sellable_num": item.get("sellableNum", 0),
        "available_stock": item.get("totalAvailableStock", 0),
        "lock_stock": item.get("totalLockStock", 0),
        "purchase_num": item.get("purchaseNum", 0),
        "on_the_way_num": item.get("onTheWayNum", 0),
        "defective_stock": item.get("totalDefectiveStock", 0),
        "virtual_stock": item.get("virtualStock", 0),
        "stock_status": item.get("stockStatus", 0),
        "purchase_price": item.get("purchasePrice"),
        "selling_price": item.get("sellingPrice"),
        "market_price": item.get("marketPrice"),
        "allocate_num": item.get("allocateNum", 0),
        "refund_stock": item.get("refundStock", 0),
        "purchase_stock": item.get("purchaseStock", 0),
        "supplier_codes": item.get("supplierCodes"),
        "supplier_names": item.get("supplierNames"),
        "warehouse_id": item.get("wareHouseId") or "",
        "stock_modified_time": _ms_to_iso(item.get("stockModifiedTime")),
        "cid_name": item.get("cidName"),
        "extra_json": _pick(
            item, "brand", "cidName", "unit", "place",
            "itemBarcode", "skuBarcode",
        ),
    }


_STOCK_UPSERT_KEY = "outer_id,sku_outer_id,warehouse_id,org_id"
_CHECKPOINT_INTERVAL = 10  # 每 10 批（~1000 编码）存盘一次


async def _get_warehouse_ids(svc: ErpSyncService) -> list[str]:
    """获取当前企业配置的仓库 ID 列表"""
    from core.config import get_settings

    wh_config = None
    if svc.org_id:
        try:
            from services.org.config_resolver import AsyncOrgConfigResolver
            resolver = AsyncOrgConfigResolver(svc.db)
            wh_config = await resolver.get(svc.org_id, "erp_warehouse_ids")
        except Exception:
            pass
    if not wh_config:
        settings = get_settings()
        wh_config = settings.erp_warehouse_ids or ""
    return [wid.strip() for wid in wh_config.split(",") if wid.strip()]


async def _fetch_stock_by_codes(
    svc: ErpSyncService, codes: list[str], warehouse_ids: list[str] | None = None,
) -> int:
    """按编码批量精准查库存并 upsert（每批最多100个编码，全量刷新专用）

    按仓库遍历：对每个仓库分别查询，确保多仓数据完整。
    分批存盘：每 _CHECKPOINT_INTERVAL 批 upsert 一次，避免单次 429 丢弃全部进度。
    """
    from services.kuaimai.erp_sync_utils import _API_SEM

    if not warehouse_ids:
        warehouse_ids = await _get_warehouse_ids(svc)
    if not warehouse_ids:
        logger.warning("stock_full: no warehouse_ids configured, skip")
        return 0

    total_batches = (len(codes) + 99) // 100
    total_upserted = 0

    logger.info(
        f"stock_full fetch start | codes={len(codes)} batches={total_batches} "
        f"warehouses={warehouse_ids}"
    )

    for wh_id in warehouse_ids:
        buffer: list[dict[str, Any]] = []
        for batch_idx in range(total_batches):
            batch = codes[batch_idx * 100 : (batch_idx + 1) * 100]
            batch_str = ",".join(batch)
            page = 0
            while page < 500:
                page += 1
                async with _API_SEM:
                    data = await svc._get_client().request_with_retry(
                        "stock.api.status.query",
                        {
                            "mainOuterId": batch_str,
                            "warehouseId": int(wh_id),
                            "pageSize": 100,
                            "pageNo": page,
                        },
                    )
                items = data.get("stockStatusVoList") or []
                for item in items:
                    row = _map_stock_item(item)
                    if row:
                        buffer.append(row)
                if len(items) < 100:
                    break
                # 全量同步请求间延迟，避免触发快麦429限流
                await asyncio.sleep(0.5)

            # 每批间延迟（全量同步非紧急，优先稳定性）
            await asyncio.sleep(0.3)

            # checkpoint：每 N 批或最后一批，立即存盘
            is_checkpoint = (batch_idx + 1) % _CHECKPOINT_INTERVAL == 0
            is_last = batch_idx == total_batches - 1
            if buffer and (is_checkpoint or is_last):
                count = await _batch_upsert(
                    svc.db, "erp_stock_status", buffer, _STOCK_UPSERT_KEY,
                    org_id=svc.org_id,
                )
                total_upserted += count
                buffer = []

        logger.info(f"stock_full warehouse {wh_id} done | upserted_so_far={total_upserted}")

    logger.info(f"stock_full fetch done | upserted={total_upserted}")
    return total_upserted


async def sync_stock(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """库存增量同步：按仓库遍历时间查询 → 直接 upsert

    快麦 API 不传 warehouseId 时只返回默认仓库的变动，
    遍历所有仓库才能捕获完整变动（实测覆盖率 92%+，配合全量兜底达 100%）。
    2026-04-14：快麦已修复时间查询返回实时数据（非历史快照），
    验证20条×14字段 100% 一致，去掉二次精查，一步到位 upsert。
    """
    from core.config import get_settings
    from services.kuaimai.erp_sync_utils import _API_SEM

    # 优先读企业配置，降级到全局 settings
    wh_config = None
    if svc.org_id:
        try:
            from services.org.config_resolver import AsyncOrgConfigResolver
            resolver = AsyncOrgConfigResolver(svc.db)
            wh_config = await resolver.get(svc.org_id, "erp_warehouse_ids")
        except Exception:
            pass
    if not wh_config:
        settings = get_settings()
        wh_config = settings.erp_warehouse_ids or ""

    wh_ids = [wid.strip() for wid in wh_config.split(",") if wid.strip()]
    if not wh_ids:
        logger.warning(
            f"sync_stock: warehouse IDs empty | org_id={svc.org_id}, skip incremental"
        )
        return 0

    # 遍历每个仓库，时间查询直接收集库存行并 upsert
    rows: list[dict[str, Any]] = []
    for wh_id in wh_ids:
        try:
            page = 0
            while page < 500:
                page += 1
                async with _API_SEM:
                    data = await svc._get_client().request_with_retry(
                        "stock.api.status.query",
                        {
                            "warehouseId": int(wh_id),
                            "startStockModified": _fmt_dt(start),
                            "endStockModified": _fmt_dt(end),
                            "pageSize": 100,
                            "pageNo": page,
                        },
                    )
                items = data.get("stockStatusVoList") or []
                for item in items:
                    row = _map_stock_item(item)
                    if row:
                        rows.append(row)
                if len(items) < 100:
                    break
        except Exception as e:
            logger.warning(
                f"sync_stock: warehouse {wh_id} query failed, skip | error={e}"
            )

    if not rows:
        return 0

    logger.info(f"sync_stock incremental | rows={len(rows)}")
    return await _batch_upsert(
        svc.db, "erp_stock_status", rows, "outer_id,sku_outer_id,warehouse_id,org_id",
        org_id=svc.org_id,
    )


async def sync_stock_full(svc: ErpSyncService) -> int:
    """库存全量刷新：按仓库遍历所有活跃编码 → 查最新值 → upsert + 清理不在配置中的仓库数据

    作为增量同步的兜底，定期执行确保数据完整。
    全量刷新后删除不在配置仓库列表中的残留数据，防止僵尸库存。
    """
    warehouse_ids = await _get_warehouse_ids(svc)
    if not warehouse_ids:
        logger.warning("sync_stock_full: no warehouse_ids configured, skip")
        return 0

    try:
        q = svc.db.table("erp_products").select("outer_id").eq("active_status", 1)
        result = await q.limit(50000).execute()
        codes = [r["outer_id"] for r in (result.data or []) if r.get("outer_id")]
    except Exception as e:
        logger.error(f"sync_stock_full: failed to load product codes | error={e}")
        return 0

    if not codes:
        return 0

    logger.info(f"sync_stock_full | total_codes={len(codes)} warehouses={warehouse_ids}")
    count = await _fetch_stock_by_codes(svc, codes, warehouse_ids)


    # 清理不在配置中的仓库残留数据（防止僵尸库存累积）
    try:
        q = svc.db.table("erp_stock_status").delete().not_.in_(
            "warehouse_id", warehouse_ids
        )
        result = await q.execute()
        deleted = len(result.data) if result.data else 0
        if deleted:
            logger.info(f"sync_stock_full: cleaned {deleted} rows from unconfigured warehouses")
    except Exception as e:
        logger.warning(f"sync_stock_full: stale cleanup failed | error={e}")

    return count
