"""
ERP 配置数据同步处理器（5种）

shop / warehouse / tag / category / logistics_company
低频全量同步，写入独立主数据表（非 erp_document_items）。
模式与 sync_supplier 一致：fetch_all → map → batch_upsert。

设计文档: docs/document/TECH_ERP数据本地索引系统.md
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import _batch_upsert, _pick
from utils.time_context import now_cn

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 店铺同步 (shop) ──────────────────────────────────────

# state 枚举：1=停用, 2=未初始化, 3=启用, 4=会话失效
_PLATFORM_MAP = {
    "taobao": "tb", "tmall": "tb", "jd": "jd",
    "pdd": "pdd", "douyin": "fxg", "kuaishou": "kuaishou",
    "xhs": "xhs", "1688": "1688", "weidian": "wd",
}


async def sync_shop(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """店铺全量同步：erp.shop.list.query → erp_shops"""
    items = await svc.fetch_all_pages(
        "erp.shop.list.query", {},
        response_key="list",
        page_size=500,
    )
    if not items:
        return 0

    rows: list[dict[str, Any]] = []
    for s in items:
        shop_id = s.get("shopId") or s.get("id")
        if not shop_id:
            continue
        source = (s.get("source") or "").lower()
        rows.append({
            "shop_id": str(shop_id),
            "name": s.get("title") or "",
            "short_name": s.get("shortTitle"),
            "platform": _PLATFORM_MAP.get(source, source),
            "nick": s.get("nick"),
            "state": s.get("state", 3),
            "group_name": s.get("groupName"),
            "deadline": str(s.get("deadline", "")) if s.get("deadline") else None,
            "extra_json": _pick(s, "userId", "source", "serviceName"),
            "synced_at": now_cn().isoformat(),
        })

    count = await _batch_upsert(
        svc.db, "erp_shops", rows,
        "shop_id,org_id",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Shop sync | count={count}")
    return count


# ── 仓库同步 (warehouse) ────────────────────────────────

# type 枚举：0=自有, 1=第三方, 2=门店
# status 枚举：0=停用, 1=正常, 2=禁止发货


async def sync_warehouse(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """仓库全量同步：erp.warehouse.list.query → erp_warehouses（含虚拟仓）"""
    # 实体仓
    real_items = await svc.fetch_all_pages(
        "erp.warehouse.list.query", {},
        response_key="list",
        page_size=500,
    )
    # 虚拟仓
    virtual_items = await svc.fetch_all_pages(
        "erp.virtual.warehouse.query", {},
        response_key="list",
        page_size=500,
    )

    rows: list[dict[str, Any]] = []
    now = now_cn().isoformat()

    for w in (real_items or []):
        wid = w.get("id")
        if not wid:
            continue
        rows.append({
            "warehouse_id": str(wid),
            "name": w.get("name") or "",
            "code": w.get("code"),
            "warehouse_type": w.get("type", 0),
            "status": w.get("status", 1),
            "contact": w.get("contact"),
            "contact_phone": w.get("contactPhone"),
            "province": w.get("state"),
            "city": w.get("city"),
            "district": w.get("district"),
            "address": w.get("address"),
            "is_virtual": False,
            "external_code": w.get("externalCode"),
            "extra_json": _pick(w, "logisticsCode", "shortName"),
            "synced_at": now,
        })

    for v in (virtual_items or []):
        vid = v.get("id")
        if not vid:
            continue
        rows.append({
            "warehouse_id": f"v_{vid}",
            "name": v.get("name") or "",
            "code": None,
            "warehouse_type": 0,
            "status": 1,
            "contact": None,
            "contact_phone": None,
            "province": None,
            "city": None,
            "district": None,
            "address": None,
            "is_virtual": True,
            "external_code": None,
            "extra_json": _pick(v, "remark", "warehouseId"),
            "synced_at": now,
        })

    if not rows:
        return 0
    count = await _batch_upsert(
        svc.db, "erp_warehouses", rows,
        "warehouse_id,org_id",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Warehouse sync | real={len(real_items or [])} virtual={len(virtual_items or [])} upserted={count}")
    return count


# ── 标签同步 (tag) ───────────────────────────────────────


async def sync_tag(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """标签全量同步：订单标签 + 商品标签 → erp_tags"""
    # 订单/售后标签
    order_tags = await svc.fetch_all_pages(
        "erp.trade.query.tag.list", {},
        response_key="list",
        page_size=500,
    )
    # 商品标签
    product_tags = await svc.fetch_all_pages(
        "erp.item.tag.list", {},
        response_key="list",
        page_size=500,
    )

    rows: list[dict[str, Any]] = []
    now = now_cn().isoformat()

    for t in (order_tags or []):
        tid = t.get("id")
        if not tid:
            continue
        rows.append({
            "tag_id": str(tid),
            "name": t.get("tagName") or t.get("name") or "",
            "tag_source": "order",
            "tag_type": t.get("type", 0),
            "color": t.get("color"),
            "remark": t.get("remark"),
            "extra_json": _pick(t, "sort", "groupId"),
            "synced_at": now,
        })

    for t in (product_tags or []):
        tid = t.get("id")
        if not tid:
            continue
        rows.append({
            "tag_id": str(tid),
            "name": t.get("name") or t.get("tagName") or "",
            "tag_source": "product",
            "tag_type": t.get("type", 0),
            "color": t.get("color"),
            "remark": t.get("remark"),
            "extra_json": _pick(t, "sort"),
            "synced_at": now,
        })

    if not rows:
        return 0
    count = await _batch_upsert(
        svc.db, "erp_tags", rows,
        "tag_id,tag_source,org_id",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Tag sync | order={len(order_tags or [])} product={len(product_tags or [])} upserted={count}")
    return count


# ── 分类同步 (category) ─────────────────────────────────


async def sync_category(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """分类全量同步：自定义分类 + 系统类目 → erp_categories"""
    # 自定义分类 (seller_cat)
    seller_cats = await svc.fetch_all_pages(
        "erp.item.seller.cat.list.get", {},
        response_key="sellerCats",
        page_size=500,
    )
    # 系统类目 (classify)
    classifies = await svc.fetch_all_pages(
        "item.classify.list.get", {},
        response_key="classifies",
        page_size=500,
    )

    rows: list[dict[str, Any]] = []
    now = now_cn().isoformat()

    for c in (seller_cats or []):
        cid = c.get("cid") or c.get("id")
        if not cid:
            continue
        rows.append({
            "cat_id": str(cid),
            "name": c.get("name") or "",
            "parent_name": c.get("parentName"),
            "full_name": c.get("fullName"),
            "cat_source": "seller_cat",
            "extra_json": _pick(c, "parentCid", "isParent", "sortOrder"),
            "synced_at": now,
        })

    for c in (classifies or []):
        cid = c.get("id") or c.get("cid")
        if not cid:
            continue
        rows.append({
            "cat_id": str(cid),
            "name": c.get("name") or "",
            "parent_name": None,
            "full_name": None,
            "cat_source": "classify",
            "extra_json": _pick(c, "parentId", "level"),
            "synced_at": now,
        })

    if not rows:
        return 0
    count = await _batch_upsert(
        svc.db, "erp_categories", rows,
        "cat_id,cat_source,org_id",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Category sync | seller_cat={len(seller_cats or [])} classify={len(classifies or [])} upserted={count}")
    return count


# ── 物流公司同步 (logistics_company) ─────────────────────


async def sync_logistics_company(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """物流公司全量同步：按仓库遍历拉取 → erp_logistics_companies

    此 API 需要传 warehouseId，不传则可能返回空。
    遍历 erp_warehouses 的仓库 ID 逐个查询。
    """
    from services.kuaimai.erp_sync_utils import _API_SEM

    # 先尝试不传 warehouseId 拉全量
    items = await svc.fetch_all_pages(
        "erp.trade.logistics.company.user.list", {},
        response_key="list",
        page_size=500,
    )

    logger.info(f"logistics_company initial fetch | count={len(items)} | response_sample={items[:1] if items else 'empty'}")

    # 如果空，按仓库逐个拉
    if not items:
        try:
            q = svc.db.table("erp_warehouses").select("warehouse_id").eq("is_virtual", False)
            result = await q.execute()
            wh_ids = [r["warehouse_id"] for r in (result.data or []) if r.get("warehouse_id")]
        except Exception:
            wh_ids = []

        client = svc._get_client()
        for wh_id in wh_ids:
            try:
                async with _API_SEM:
                    data = await client.request_with_retry(
                        "erp.trade.logistics.company.user.list",
                        {"warehouseId": int(wh_id)},
                    )
                wh_items = data.get("list") or []
                items.extend(wh_items)
            except Exception as e:
                logger.debug(f"Logistics company fetch skip | wh_id={wh_id} | error={e}")

    if not items:
        return 0

    rows: list[dict[str, Any]] = []
    now = now_cn().isoformat()
    seen_ids: set[str] = set()
    for lc in items:
        cid = lc.get("id") or lc.get("logisticsId")
        if not cid:
            continue
        cid_str = str(cid)
        if cid_str in seen_ids:
            continue
        seen_ids.add(cid_str)
        rows.append({
            "company_id": cid_str,
            "name": lc.get("logisticsName") or lc.get("name") or "",
            "code": lc.get("logisticsCode") or lc.get("code"),
            "extra_json": _pick(lc, "logisticsType", "warehouseId", "warehouseName"),
            "synced_at": now,
        })

    if not rows:
        return 0
    count = await _batch_upsert(
        svc.db, "erp_logistics_companies", rows,
        "company_id,org_id",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Logistics company sync | count={count}")
    return count
