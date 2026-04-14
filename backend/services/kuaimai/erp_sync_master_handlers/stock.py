"""ERP 库存同步：增量（时间查询直接 upsert）+ 全量（活跃商品全扫）"""

from __future__ import annotations

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


async def _fetch_stock_by_codes(
    svc: ErpSyncService, codes: list[str],
) -> int:
    """按编码批量精准查库存并 upsert（每批最多100个编码，全量刷新专用）"""
    from services.kuaimai.erp_sync_utils import _API_SEM

    rows: list[dict[str, Any]] = []
    for i in range(0, len(codes), 100):
        batch = codes[i : i + 100]
        batch_str = ",".join(batch)
        # 按编码查可能返回多页（多 SKU × 多仓库）
        page = 0
        while page < 500:
            page += 1
            async with _API_SEM:
                data = await svc._get_client().request_with_retry(
                    "stock.api.status.query",
                    {"mainOuterId": batch_str, "pageSize": 100, "pageNo": page},
                )
            items = data.get("stockStatusVoList") or []
            for item in items:
                row = _map_stock_item(item)
                if row:
                    rows.append(row)
            if len(items) < 100:
                break

    if not rows:
        return 0
    return await _batch_upsert(
        svc.db, "erp_stock_status", rows, "outer_id,sku_outer_id,warehouse_id,org_id",
        org_id=svc.org_id,
    )


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
    """库存全量刷新：从商品表取所有活跃编码 → 按编码批量查最新值 → upsert

    作为增量同步的兜底，定期执行确保数据完整。
    12000 编码 ÷ 100/批 = 120 次 API 调用，串行约 10 秒。
    """
    try:
        q = svc.db.table("erp_products").select("outer_id").eq("active_status", 1)
        result = await q.limit(50000).execute()
        codes = [r["outer_id"] for r in (result.data or []) if r.get("outer_id")]
    except Exception as e:
        logger.error(f"sync_stock_full: failed to load product codes | error={e}")
        return 0

    if not codes:
        return 0

    logger.info(f"sync_stock_full | total_codes={len(codes)}")
    return await _fetch_stock_by_codes(svc, codes)
