"""
ERP 主数据同步处理器（4种）

product / stock / supplier / platform_map
每个处理器写入对应的主数据表（非 erp_document_items）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 工具函数 ────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str | None) -> str | None:
    """清洗 HTML 标签（商品备注可能含 HTML）"""
    if not text:
        return text
    return _HTML_TAG_RE.sub("", text).strip()


def _fmt_dt(dt: datetime) -> str:
    """yyyy-MM-dd HH:mm:ss 时间格式（快麦API统一要求）"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _pick(src: dict, *keys: str) -> dict:
    """提取存在且非 None 的键值对"""
    return {k: src[k] for k in keys if k in src and src[k] is not None}


def _batch_upsert(
    db: Any, table: str, rows: list[dict], on_conflict: str,
    batch_size: int = 100,
) -> int:
    """通用批量 upsert（与 ErpSyncService.upsert_document_items 类似）"""
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            db.table(table).upsert(batch, on_conflict=on_conflict).execute()
            total += len(batch)
        except Exception as e:
            logger.error(
                f"Upsert {table} failed | batch={i // batch_size} | "
                f"rows={len(batch)} | error={e}"
            )
    return total


# ── 商品同步 (product) ──────────────────────────────────


async def sync_product(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """商品增量同步：item.list.query → erp_products + erp_product_skus"""
    products = await svc.fetch_all_pages(
        "item.list.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
        response_key="items",
        page_size=500,
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
        spu_rows.append({
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
            "created_at": p.get("created"),
            "modified_at": p.get("modified"),
            "pic_url": p.get("picPath"),
            "suit_singles": p.get("singleList"),
            "extra_json": _pick(
                p, "sellerCats", "classify", "standard", "safekind",
                "x", "y", "z", "boxnum", "customAttribute",
            ),
        })

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

    spu_count = _batch_upsert(svc.db, "erp_products", spu_rows, "outer_id")
    sku_count = _batch_upsert(svc.db, "erp_product_skus", sku_rows, "sku_outer_id")
    if spu_count or sku_count:
        logger.info(f"Product sync | spu={spu_count} sku={sku_count}")
    return spu_count + sku_count


# ── 库存同步 (stock) ────────────────────────────────────


async def sync_stock(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """库存增量同步：stock.api.status.query → erp_stock_status"""
    items = await svc.fetch_all_pages(
        "stock.api.status.query",
        {
            "startStockModified": _fmt_dt(start),
            "endStockModified": _fmt_dt(end),
        },
        response_key="stockStatusVoList",
        page_size=50,
    )
    if not items:
        return 0

    rows: list[dict[str, Any]] = []
    for item in items:
        outer_id = item.get("mainOuterId") or item.get("outerId")
        if not outer_id:
            continue
        rows.append({
            "outer_id": outer_id,
            "sku_outer_id": item.get("skuOuterId") or "",  # NOT NULL DEFAULT ''
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
            "stock_modified_time": item.get("stockModifiedTime"),
            "extra_json": _pick(
                item, "brand", "cidName", "unit", "place",
                "itemBarcode", "skuBarcode",
            ),
        })

    count = _batch_upsert(
        svc.db, "erp_stock_status", rows, "outer_id,sku_outer_id,warehouse_id",
    )
    return count


# ── 供应商同步 (supplier) ────────────────────────────────


async def sync_supplier(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """供应商全量同步：supplier.list.query → erp_suppliers"""
    client = svc._get_client()
    try:
        data = await client.request_with_retry(
            "supplier.list.query", {"pageSize": 500},
        )
        suppliers = data.get("list") or data.get("suppliers") or []
    except Exception as e:
        logger.warning(f"Supplier sync failed | error={e}")
        return 0
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

    count = _batch_upsert(svc.db, "erp_suppliers", rows, "code")
    return count


# ── 平台映射同步 (platform_map) ──────────────────────────


async def sync_platform_map(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """平台映射同步：逐个商品查询 erp.item.outerid.list.get → erp_product_platform_map"""
    # 该 API 要求传 outerId 参数，无法空参数全量查询
    # 从 DB 中取已同步的商品列表逐个查询
    try:
        result = (
            svc.db.table("erp_products")
            .select("outer_id")
            .neq("active_status", -1)
            .limit(5000)
            .execute()
        )
        outer_ids = [r["outer_id"] for r in (result.data or []) if r.get("outer_id")]
    except Exception as e:
        logger.warning(f"Platform map: failed to get product list | error={e}")
        return 0
    if not outer_ids:
        return 0

    client = svc._get_client()
    rows: list[dict[str, Any]] = []
    for oid in outer_ids:
        try:
            data = await client.request_with_retry(
                "erp.item.outerid.list.get", {"outerId": oid},
            )
            items = data.get("itemOuterIdInfos") or []
        except Exception:
            continue

        for item in items:
            outer_id = item.get("outerId")
            num_iid = item.get("numIid")
            if not outer_id or not num_iid:
                continue

            sku_mappings = []
            for sku in item.get("skuOuterIdInfos") or item.get("skuList") or []:
                sku_mappings.append({
                    "skuOuterId": sku.get("skuOuterId"),
                    "skuNumIid": sku.get("skuNumIid"),
                })

            rows.append({
                "outer_id": outer_id,
                "num_iid": str(num_iid),
                "user_id": str(item.get("userId", "")),
                "title": item.get("title"),
                "sku_mappings": sku_mappings or None,
            })

    count = _batch_upsert(
        svc.db, "erp_product_platform_map", rows, "outer_id,num_iid",
    )
    return count
