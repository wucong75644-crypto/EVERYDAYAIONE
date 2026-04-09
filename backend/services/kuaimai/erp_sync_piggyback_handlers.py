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
                    {"sids": ",".join(batch), "pageNo": 1, "pageSize": 20},
                )
            logs = data.get("list") or data.get("traces") or []
            for log in logs:
                all_rows.append({
                    "system_id": str(log.get("sid") or ""),
                    "operator": log.get("operator") or log.get("operatorName"),
                    "action": log.get("action") or log.get("operateAction") or "",
                    "content": log.get("content") or log.get("operateContent"),
                    "operate_time": _safe_ts(log.get("operateTime")) or "1970-01-01",
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
        "system_id,operate_time,action,org_id",
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
                rows = []
                # DEBUG: 临时日志，查看 API 返回结构
                if not rows:  # 首次进入
                    logger.info(
                        f"piggyback_express raw | sid={sid} | "
                        f"keys={list(data.keys())[:10]} | "
                        f"has_packs={'packs' in data} | "
                        f"has_outSids={'outSids' in data} | "
                        f"has_outSid={'outSid' in data}"
                    )
                # 格式 1: packs 数组（多包裹场景）
                packs = data.get("packs") or data.get("list") or []
                if packs:
                    for pack in packs:
                        rows.append({
                            "system_id": sid,
                            "package_id": str(pack.get("packId") or pack.get("id") or ""),
                            "express_no": pack.get("outSid") or pack.get("expressNo") or "",
                            "express_company": pack.get("expressCompanyName") or pack.get("company"),
                            "express_company_code": pack.get("expressCompanyCode") or pack.get("cpCode"),
                            "items_json": pack.get("items") or pack.get("orderItems") or [],
                            "extra_json": _pick(pack, "weight", "packType", "consignTime"),
                            "synced_at": now,
                        })
                else:
                    # 格式 2: 扁平结构 {cpCode, outSids[], expressName}
                    out_sids = data.get("outSids") or []
                    express_name = data.get("expressName") or ""
                    cp_code = data.get("cpCode") or ""
                    if isinstance(out_sids, list):
                        for express_no in out_sids:
                            rows.append({
                                "system_id": sid,
                                "package_id": "",
                                "express_no": str(express_no) if express_no else "",
                                "express_company": express_name,
                                "express_company_code": cp_code,
                                "items_json": [],
                                "extra_json": {},
                                "synced_at": now,
                            })
                    elif data.get("outSid"):
                        # 格式 3: 单个 outSid 字符串
                        rows.append({
                            "system_id": sid,
                            "package_id": "",
                            "express_no": data.get("outSid") or "",
                            "express_company": express_name,
                            "express_company_code": cp_code,
                            "items_json": [],
                            "extra_json": {},
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
        "system_id,express_no,package_id,org_id",
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
                        "action": log.get("action") or log.get("operateType") or "",
                        "content": log.get("content") or log.get("operateContent"),
                        "operate_time": _safe_ts(log.get("operateTime") or log.get("created")) or "1970-01-01",
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

    # 去重：同一 (work_order_id, operate_time, action) 只保留第一条
    seen: set[tuple] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in all_rows:
        key = (row["work_order_id"], row["operate_time"], row["action"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(row)

    count = await _batch_upsert(
        svc.db, "erp_aftersale_logs", unique_rows,
        "work_order_id,operate_time,action,org_id",
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
    """批次效期库存同步：遍历 erp_shops × erp_product_platform_map 查询

    API 需要 shopId + (skuIds 或 numIids)，不能只传 shopId。
    遍历店铺，按店铺的 platform mapping 取 num_iid 批量查询。
    适用于食品/化妆品等有保质期管理的场景。
    """
    from services.kuaimai.erp_local_helpers import _apply_org

    # 取所有启用店铺
    try:
        q = svc.db.table("erp_shops").select("shop_id").eq("state", 3)
        result = await _apply_org(q, svc.org_id).execute()
        shop_ids = [r["shop_id"] for r in (result.data or []) if r.get("shop_id")]
    except Exception as e:
        logger.warning(f"sync_batch_stock: failed to load shops | error={e}")
        return 0

    if not shop_ids:
        logger.debug("sync_batch_stock: no shops found, skip")
        return 0

    # 取所有平台映射的 num_iid（按 user_id 分组）
    try:
        q = svc.db.table("erp_product_platform_map").select("num_iid,user_id")
        result = await _apply_org(q, svc.org_id).limit(50000).execute()
        # user_id 就是 shop 的 userId
        shop_numids: dict[str, list[str]] = {}
        for r in (result.data or []):
            uid = r.get("user_id") or ""
            nid = r.get("num_iid")
            if nid:
                shop_numids.setdefault(uid, []).append(nid)
    except Exception as e:
        logger.warning(f"sync_batch_stock: failed to load platform map | error={e}")
        return 0

    if not shop_numids:
        logger.debug("sync_batch_stock: no platform mappings, skip")
        return 0

    client = svc._get_client()
    all_rows: list[dict[str, Any]] = []
    now = datetime.now().isoformat()

    for user_id, num_ids in shop_numids.items():
        if not user_id:
            continue  # 跳过无店铺 ID 的映射
        # 每批最多 20 个 numIid
        for i in range(0, len(num_ids), 20):
            batch_ids = num_ids[i:i + 20]
            try:
                async with _API_SEM:
                    data = await client.request_with_retry(
                        "erp.wms.product.stock.query",
                        {"shopId": user_id, "numIids": ",".join(batch_ids)},
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
                        "batch_no": item.get("batchNo") or item.get("batchCode") or "",
                        "production_date": item.get("productionDate"),
                        "expiry_date": item.get("expiryDate") or item.get("shelfLifeDate"),
                        "shelf_life_days": item.get("shelfLifeDays"),
                        "stock_qty": item.get("stock") or item.get("quantity") or 0,
                        "warehouse_name": item.get("warehouseName") or "",
                        "shop_id": user_id or "",
                        "extra_json": _pick(
                            item, "warehouseId", "sectionCode",
                            "inboundDate", "supplierId",
                        ),
                        "synced_at": now,
                    })
            except Exception as e:
                logger.debug(f"sync_batch_stock: batch failed | shop={user_id} | error={e}")

    if not all_rows:
        return 0

    count = await _batch_upsert(
        svc.db, "erp_batch_stock", all_rows,
        "outer_id,sku_outer_id,batch_no,shop_id,warehouse_name,org_id",
        org_id=svc.org_id,
    )
    if count:
        logger.info(f"Batch stock sync | shops={len(shop_ids)} rows={count}")
    return count
