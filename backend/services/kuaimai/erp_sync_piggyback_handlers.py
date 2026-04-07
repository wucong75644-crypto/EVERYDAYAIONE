"""
ERP 搭便车 + 特殊模式同步处理器

搭便车（订单同步后触发）：
  - order_log: 订单操作日志（batch system_ids → erp_order_logs）
  - express:   订单包裹信息（batch system_ids → erp_order_packages）

搭便车（售后同步后触发）：
  - aftersale_log: 售后操作日志（batch work_order_ids → erp_aftersale_logs）

独立同步：
  - goods_section: 货位库存（标准增量 → erp_document_items）
  - batch_stock:   批次效期库存（遍历店铺全量 → erp_batch_stock）
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import (
    _API_SEM,
    _batch_upsert,
    _fmt_d,
    _pick,
    _safe_ts,
)

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 订单操作日志（搭便车）─────────────────────────────────


async def piggyback_order_log(
    svc: ErpSyncService, system_ids: list[str],
) -> int:
    """订单同步后搭便车：批量查操作日志，200 个/批"""
    if not system_ids:
        return 0

    client = svc._get_client()
    all_rows: list[dict[str, Any]] = []
    now = datetime.now().isoformat()

    for i in range(0, len(system_ids), 200):
        batch = system_ids[i:i + 200]
        try:
            async with _API_SEM:
                data = await client.request_with_retry(
                    "erp.trade.trace.list",
                    {"sids": ",".join(batch), "pageSize": 500},
                )
            logs = data.get("list") or data.get("traces") or []
            for log in logs:
                all_rows.append({
                    "system_id": str(log.get("sid") or ""),
                    "operator": log.get("operator") or log.get("operatorName"),
                    "action": log.get("action") or log.get("operateAction"),
                    "content": log.get("content") or log.get("operateContent"),
                    "operate_time": _safe_ts(log.get("operateTime")),
                    "extra_json": _pick(log, "traceId", "ip"),
                    "synced_at": now,
                })
        except Exception as e:
            logger.warning(
                f"piggyback_order_log failed | batch_size={len(batch)} | error={e}"
            )

    if not all_rows:
        return 0

    count = await _batch_upsert(
        svc.db, "erp_order_logs", all_rows,
        "system_id,COALESCE(operate_time, '1970-01-01'),COALESCE(action, ''),"
        "COALESCE(org_id, '00000000-0000-0000-0000-000000000000')",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Order log piggyback | sids={len(system_ids)} logs={count}")
    return count


# ── 订单包裹信息（搭便车）─────────────────────────────────


async def piggyback_express(
    svc: ErpSyncService, system_ids: list[str],
) -> int:
    """订单同步后搭便车：逐个查包裹信息（API 不支持批量）"""
    if not system_ids:
        return 0

    client = svc._get_client()
    all_rows: list[dict[str, Any]] = []
    now = datetime.now().isoformat()

    async def _fetch_one(sid: str) -> list[dict]:
        async with _API_SEM:
            try:
                data = await client.request_with_retry(
                    "erp.trade.multi.packs.query", {"sid": sid},
                )
                packs = data.get("packs") or data.get("list") or []
                rows = []
                for pack in packs:
                    rows.append({
                        "system_id": sid,
                        "package_id": str(pack.get("packId") or pack.get("id") or ""),
                        "express_no": pack.get("outSid") or pack.get("expressNo"),
                        "express_company": pack.get("expressCompanyName") or pack.get("company"),
                        "express_company_code": pack.get("expressCompanyCode"),
                        "items_json": pack.get("items") or pack.get("orderItems") or [],
                        "extra_json": _pick(pack, "weight", "packType", "consignTime"),
                        "synced_at": now,
                    })
                # 如果 API 返回的是扁平结构（无 packs 数组）
                if not packs and data.get("outSid"):
                    rows.append({
                        "system_id": sid,
                        "package_id": "",
                        "express_no": data.get("outSid"),
                        "express_company": data.get("expressCompanyName"),
                        "express_company_code": data.get("expressCompanyCode"),
                        "items_json": [],
                        "extra_json": _pick(data, "weight", "consignTime"),
                        "synced_at": now,
                    })
                return rows
            except Exception as e:
                logger.debug(f"piggyback_express skip | sid={sid} | error={e}")
                return []

    # 并发拉取，受 _API_SEM 限流
    tasks = [_fetch_one(sid) for sid in system_ids]
    results = await asyncio.gather(*tasks)
    for rows in results:
        all_rows.extend(rows)

    if not all_rows:
        return 0

    count = await _batch_upsert(
        svc.db, "erp_order_packages", all_rows,
        "system_id,COALESCE(express_no, ''),COALESCE(package_id, ''),"
        "COALESCE(org_id, '00000000-0000-0000-0000-000000000000')",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Express piggyback | sids={len(system_ids)} packages={count}")
    return count


# ── 售后操作日志（搭便车）─────────────────────────────────


async def piggyback_aftersale_log(
    svc: ErpSyncService, work_order_ids: list[str],
) -> int:
    """售后同步后搭便车：逐个查操作日志"""
    if not work_order_ids:
        return 0

    client = svc._get_client()
    all_rows: list[dict[str, Any]] = []
    now = datetime.now().isoformat()

    async def _fetch_one(wid: str) -> list[dict]:
        async with _API_SEM:
            try:
                data = await client.request_with_retry(
                    "erp.aftersale.operate.log.query", {"workOrderId": wid},
                )
                logs = data.get("list") or data.get("logs") or []
                rows = []
                for log in logs:
                    rows.append({
                        "work_order_id": wid,
                        "operator": log.get("operatorName") or log.get("operator"),
                        "action": log.get("action") or log.get("operateType"),
                        "content": log.get("content") or log.get("operateContent"),
                        "operate_time": _safe_ts(log.get("operateTime") or log.get("created")),
                        "extra_json": _pick(log, "logId", "ip"),
                        "synced_at": now,
                    })
                return rows
            except Exception as e:
                logger.debug(f"piggyback_aftersale_log skip | wid={wid} | error={e}")
                return []

    tasks = [_fetch_one(wid) for wid in work_order_ids]
    results = await asyncio.gather(*tasks)
    for rows in results:
        all_rows.extend(rows)

    if not all_rows:
        return 0

    count = await _batch_upsert(
        svc.db, "erp_aftersale_logs", all_rows,
        "work_order_id,COALESCE(operate_time, '1970-01-01'),COALESCE(action, ''),"
        "COALESCE(org_id, '00000000-0000-0000-0000-000000000000')",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Aftersale log piggyback | wids={len(work_order_ids)} logs={count}")
    return count


# ── 货位库存（标准增量同步）──────────────────────────────


async def sync_goods_section(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """货位库存同步：标准增量，写入 erp_document_items"""
    records = await svc.fetch_all_pages(
        "asso.goods.section.sku.query",
        {"startModified": _fmt_d(start), "endModified": _fmt_d(end)},
    )
    if not records:
        return 0

    all_rows: list[dict[str, Any]] = []
    for i, r in enumerate(records):
        rid = r.get("id") or f"gs_{i}"
        all_rows.append({
            "doc_type": "goods_section",
            "doc_id": str(rid),
            "doc_code": r.get("sectionCode"),
            "doc_status": None,
            "doc_created_at": _safe_ts(r.get("modified") or r.get("created")),
            "doc_modified_at": _safe_ts(r.get("modified")),
            "item_index": 0,
            "outer_id": r.get("outerId") or r.get("itemOuterId"),
            "sku_outer_id": r.get("skuOuterId") or r.get("outerId"),
            "item_name": r.get("title") or r.get("itemTitle"),
            "quantity": r.get("stock") or r.get("quantity"),
            "warehouse_name": r.get("warehouseName"),
            "extra_json": _pick(
                r, "sectionCode", "sectionName", "sectionType",
                "warehouseId", "batchNo", "lockStock",
            ),
        })

    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 批次效期库存（遍历店铺全量同步）─────────────────────


async def sync_batch_stock(svc: ErpSyncService) -> int:
    """批次效期库存同步：遍历 erp_shops 的 shop_id 全量查询

    类似 stock_full 模式：无时间窗口，按店铺 ID 遍历。
    适用于食品/化妆品等有保质期管理的场景。
    """
    # 从 erp_shops 取所有启用店铺 ID
    try:
        from services.kuaimai.erp_local_helpers import _apply_org
        q = svc.db.table("erp_shops").select("shop_id").eq("state", 3)
        result = await _apply_org(q, svc.org_id).execute()
        shop_ids = [r["shop_id"] for r in (result.data or []) if r.get("shop_id")]
    except Exception as e:
        logger.warning(f"sync_batch_stock: failed to load shops | error={e}")
        return 0

    if not shop_ids:
        logger.debug("sync_batch_stock: no shops found, skip")
        return 0

    client = svc._get_client()
    all_rows: list[dict[str, Any]] = []
    now = datetime.now().isoformat()

    for shop_id in shop_ids:
        try:
            page = 0
            while page < 500:
                page += 1
                async with _API_SEM:
                    data = await client.request_with_retry(
                        "erp.wms.product.stock.query",
                        {"shopId": shop_id, "pageNo": page, "pageSize": 100},
                    )
                items = data.get("list") or []
                for item in items:
                    outer_id = item.get("outerId") or item.get("itemOuterId")
                    if not outer_id:
                        continue
                    all_rows.append({
                        "outer_id": outer_id,
                        "sku_outer_id": item.get("skuOuterId") or "",
                        "item_name": item.get("title"),
                        "batch_no": item.get("batchNo") or item.get("batchCode"),
                        "production_date": item.get("productionDate"),
                        "expiry_date": item.get("expiryDate") or item.get("shelfLifeDate"),
                        "shelf_life_days": item.get("shelfLifeDays"),
                        "stock_qty": item.get("stock") or item.get("quantity") or 0,
                        "warehouse_name": item.get("warehouseName"),
                        "shop_id": shop_id,
                        "extra_json": _pick(
                            item, "warehouseId", "sectionCode",
                            "inboundDate", "supplierId",
                        ),
                        "synced_at": now,
                    })
                if len(items) < 100:
                    break
        except Exception as e:
            logger.warning(f"sync_batch_stock: shop {shop_id} failed | error={e}")

    if not all_rows:
        return 0

    count = await _batch_upsert(
        svc.db, "erp_batch_stock", all_rows,
        "outer_id,sku_outer_id,COALESCE(batch_no, ''),"
        "COALESCE(shop_id, ''),COALESCE(warehouse_name, ''),"
        "COALESCE(org_id, '00000000-0000-0000-0000-000000000000')",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Batch stock sync | shops={len(shop_ids)} rows={count}")
    return count
