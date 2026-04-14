"""ERP 商品同步：item.list.query → erp_products + erp_product_skus"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import (
    _batch_upsert,
    _fmt_dt,
    _pick,
    _safe_ts,
    _strip_html,
)

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


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
            "is_sku_item": bool(p.get("isSkuItem")),
            "length": p.get("x"),
            "width": p.get("y"),
            "height": p.get("z"),
            "classify_name": (p.get("classify") or {}).get("name"),
            "seller_cat_name": (
                (p.get("sellerCats") or [{}])[-1].get("fullName")
                if p.get("sellerCats") else None
            ),
            "extra_json": _pick(
                p, "sellerCats", "classify", "standard", "safekind",
                "boxnum", "customAttribute",
            ),
        }
        # suit_singles: 从 SKU 级别的 suitSingleList 汇总（2026-04-14 快麦修复后）
        # 仅当任一 SKU 实际返回 suitSingleList 时才写入，避免覆盖 CSV 导入数据
        # 按 skuOuterId 去重（同一子商品会出现在多个 SKU 组合中）
        seen_singles: set[str] = set()
        all_suit_singles: list[dict] = []

        # SKU 行（商品 list API 含 skus 数组）
        for sku in p.get("skus") or []:
            sku_outer_id = sku.get("skuOuterId")
            if not sku_outer_id:
                continue

            # 收集套件子商品信息
            suit_single_list = sku.get("suitSingleList")
            if suit_single_list:
                for s in suit_single_list:
                    key = s.get("skuOuterId", "")
                    if key not in seen_singles:
                        seen_singles.add(key)
                        all_suit_singles.append(s)

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
                "length": sku.get("x"),
                "width": sku.get("y"),
                "height": sku.get("z"),
                "sku_remark": sku.get("skuRemark") or None,
                "extra_json": _pick(
                    sku, "skuComponent", "skuRemark", "propertiesAlias",
                    "boxnum", "suitSingleList",
                ),
            })

        if all_suit_singles:
            spu_row["suit_singles"] = all_suit_singles
        spu_rows.append(spu_row)

    spu_count = await _batch_upsert(
        svc.db, "erp_products", spu_rows, "outer_id,org_id", org_id=svc.org_id,
    )
    sku_count = await _batch_upsert(
        svc.db, "erp_product_skus", sku_rows, "sku_outer_id,org_id", org_id=svc.org_id,
    )
    if spu_count or sku_count:
        logger.info(f"Product sync | spu={spu_count} sku={sku_count}")
    return spu_count + sku_count
