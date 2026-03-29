"""
ERP 主数据同步处理器（4种）

product / stock / supplier / platform_map
每个处理器写入对应的主数据表（非 erp_document_items）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import (  # noqa: F401 — re-export for backward compat
    _batch_upsert,
    _fmt_dt,
    _ms_to_iso,
    _pick,
    _safe_ts,
    _strip_html,
)

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 商品同步 (product) ──────────────────────────────────


async def sync_product(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """商品增量同步：item.list.query → erp_products + erp_product_skus"""
    products = await svc.fetch_all_pages(
        "item.list.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
        response_key="items",
        page_size=200,
    )
    if not products:
        return 0

    spu_rows: list[dict[str, Any]] = []
    sku_rows: list[dict[str, Any]] = []

    for p in products:
        outer_id = p.get("outerId")
        if not outer_id:
            continue

        # SPU 行
        spu_row: dict[str, Any] = {
            "outer_id": outer_id,
            "title": p.get("title"),
            "item_type": p.get("type", 0),
            "is_virtual": bool(p.get("isVirtual")),
            "active_status": p.get("activeStatus", 1),
            "barcode": p.get("barcode"),
            "purchase_price": p.get("purchasePrice"),
            "selling_price": p.get("priceOutput"),
            "market_price": p.get("marketPrice"),
            "weight": p.get("weight"),
            "unit": p.get("unit"),
            "is_gift": bool(p.get("makeGift")),
            "sys_item_id": p.get("sysItemId"),
            "brand": p.get("brand"),
            "shipper": p.get("shipper"),
            "remark": _strip_html(p.get("remark")),
            "created_at": _safe_ts(p.get("created")),
            "modified_at": _safe_ts(p.get("modified")),
            "pic_url": p.get("picPath"),
            "extra_json": _pick(
                p, "sellerCats", "classify", "standard", "safekind",
                "x", "y", "z", "boxnum", "customAttribute",
            ),
        }
        # suit_singles: 仅当 API 实际返回 singleList 时才写入，
        # 避免 None 覆盖掉从 CSV 导入的子商品数据
        if p.get("singleList") is not None:
            spu_row["suit_singles"] = p["singleList"]
        spu_rows.append(spu_row)

        # SKU 行（商品 list API 含 skus 数组）
        for sku in p.get("skus") or []:
            sku_outer_id = sku.get("skuOuterId")
            if not sku_outer_id:
                continue
            sku_rows.append({
                "outer_id": outer_id,
                "sku_outer_id": sku_outer_id,
                "properties_name": sku.get("propertiesName"),
                "barcode": sku.get("barcode"),
                "purchase_price": sku.get("purchasePrice"),
                "selling_price": sku.get("priceOutput"),
                "market_price": sku.get("marketPrice"),
                "weight": sku.get("weight"),
                "unit": sku.get("unit"),
                "shipper": sku.get("shipper"),
                "pic_url": sku.get("skuPicPath"),
                "sys_sku_id": sku.get("sysSkuId"),
                "active_status": sku.get("activeStatus", 1),
                "extra_json": _pick(
                    sku, "skuComponent", "skuRemark", "propertiesAlias",
                    "x", "y", "z", "boxnum",
                ),
            })

    spu_count = await _batch_upsert(svc.db, "erp_products", spu_rows, "outer_id", org_id=svc.org_id)
    sku_count = await _batch_upsert(svc.db, "erp_product_skus", sku_rows, "sku_outer_id", org_id=svc.org_id)
    if spu_count or sku_count:
        logger.info(f"Product sync | spu={spu_count} sku={sku_count}")
    return spu_count + sku_count


# ── 库存同步 (stock) ────────────────────────────────────


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
        "extra_json": _pick(
            item, "brand", "cidName", "unit", "place",
            "itemBarcode", "skuBarcode",
        ),
    }


async def _fetch_stock_by_codes(
    svc: ErpSyncService, codes: list[str],
) -> int:
    """按编码批量精准查库存并 upsert（每批最多100个编码）"""
    from services.kuaimai.erp_sync_handlers import _API_SEM

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
        svc.db, "erp_stock_status", rows, "outer_id,sku_outer_id,warehouse_id",
        org_id=svc.org_id,
    )


async def sync_stock(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """库存增量同步：按仓库遍历收集变动编码 → 按编码精准查最新值 → upsert

    快麦 API 不传 warehouseId 时只返回默认仓库的变动，
    遍历所有仓库才能捕获完整变动（实测覆盖率 92%+，配合全量兜底达 100%）。
    时间查询返回的 sellableNum 是历史快照，所以只用来收集编码，
    再用 mainOuterId 精准查拿实时值。
    """
    from core.config import get_settings
    from services.kuaimai.erp_sync_handlers import _API_SEM

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
        logger.warning(f"sync_stock: warehouse IDs empty | org_id={svc.org_id}, skip incremental")
        return 0

    # Step 1: 遍历每个仓库，收集有变动的主编码
    changed_codes: set[str] = set()
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
                    code = item.get("mainOuterId") or item.get("outerId")
                    if code:
                        changed_codes.add(code)
                if len(items) < 100:
                    break
        except Exception as e:
            logger.warning(
                f"sync_stock: warehouse {wh_id} query failed, skip | error={e}"
            )

    if not changed_codes:
        return 0

    logger.info(f"sync_stock incremental | changed_codes={len(changed_codes)}")

    # Step 2: 按编码精准查最新库存值
    return await _fetch_stock_by_codes(svc, list(changed_codes))


async def sync_stock_full(svc: ErpSyncService) -> int:
    """库存全量刷新：从商品表取所有活跃编码 → 按编码批量查最新值 → upsert

    作为增量同步的兜底，定期执行确保数据完整。
    12000 编码 ÷ 100/批 = 120 次 API 调用，串行约 10 秒。
    """
    try:
        q = svc.db.table("erp_products").select("outer_id").eq("active_status", 1)
        q = svc._apply_org(q)
        result = await q.limit(50000).execute()
        codes = [r["outer_id"] for r in (result.data or []) if r.get("outer_id")]
    except Exception as e:
        logger.error(f"sync_stock_full: failed to load product codes | error={e}")
        return 0

    if not codes:
        return 0

    logger.info(f"sync_stock_full | total_codes={len(codes)}")
    return await _fetch_stock_by_codes(svc, codes)


# ── 供应商同步 (supplier) ────────────────────────────────


async def sync_supplier(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """供应商全量同步：supplier.list.query → erp_suppliers（翻页拉取）"""
    suppliers = await svc.fetch_all_pages(
        "supplier.list.query", {},
        response_key="list",
        page_size=200,  # API max=200
    )
    if not suppliers:
        return 0

    rows: list[dict[str, Any]] = []
    for s in suppliers:
        code = s.get("code")
        if not code:
            continue
        rows.append({
            "code": code,
            "name": s.get("name", ""),
            "status": s.get("status", 1),
            "contact_name": s.get("contactName"),
            "mobile": s.get("mobile"),
            "phone": s.get("phone"),
            "email": s.get("email"),
            "category_name": s.get("categoryName"),
            "bill_type": s.get("billType"),
            "plan_receive_day": s.get("planReceiveDay"),
            "address": s.get("address"),
            "remark": s.get("remark"),
        })

    count = await _batch_upsert(svc.db, "erp_suppliers", rows, "code", org_id=svc.org_id)
    return count


# ── 平台映射同步 (platform_map) ──────────────────────────


async def sync_platform_map(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """平台映射同步：批量查询 erp.item.outerid.list.get → erp_product_platform_map

    API 要求参数 outerIds（SKU 最小粒度编码，逗号分隔），不支持 SPU 编码。
    """
    # 从 DB 取所有 SKU 编码
    try:
        q = svc.db.table("erp_product_skus").select("sku_outer_id")
        result = await svc._apply_org(q).limit(10000).execute()
        sku_ids = [r["sku_outer_id"] for r in (result.data or []) if r.get("sku_outer_id")]
    except Exception as e:
        logger.warning(f"Platform map: failed to get SKU list | error={e}")
        return 0
    if not sku_ids:
        return 0

    client = svc._get_client()
    rows: list[dict[str, Any]] = []
    batch_size = 20  # 每次批量查 20 个 SKU

    for i in range(0, len(sku_ids), batch_size):
        batch = sku_ids[i:i + batch_size]
        try:
            data = await client.request_with_retry(
                "erp.item.outerid.list.get", {"outerIds": ",".join(batch)},
            )
            items = data.get("itemOuterIdInfos") or []
        except Exception:
            continue

        for item in items:
            outer_id = item.get("outerId")
            if not outer_id:
                continue
            # API 返回 tbItemList 数组，每条是一个平台商品映射
            for tb in item.get("tbItemList") or []:
                num_iid = tb.get("numIid")
                if not num_iid:
                    continue
                rows.append({
                    "outer_id": outer_id,
                    "num_iid": str(num_iid),
                    "user_id": str(tb.get("userId", "")),
                    "title": tb.get("title"),
                    "sku_mappings": [{"skuOuterId": tb.get("skuOuterId"), "skuNumIid": tb.get("skuId")}] if tb.get("skuOuterId") else None,
                })

    # API 可能返回重复映射，按 (outer_id, num_iid) 去重
    seen: set[tuple[str, str]] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        key = (row["outer_id"], row["num_iid"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(row)

    count = await _batch_upsert(
        svc.db, "erp_product_platform_map", unique_rows, "outer_id,num_iid",
        org_id=svc.org_id,
    )
    return count
