"""
ERP 搭便车 + 特殊模式同步处理器

搭便车（订单同步后触发）：
  - order_log: 订单操作日志（batch system_ids → erp_order_logs）
  - express:   订单包裹信息（batch system_ids → erp_order_packages）

搭便车（售后同步后触发）：
  - aftersale_log: 售后操作日志（batch work_order_ids → erp_aftersale_logs）

独立同步：
  - goods_section: 货位库存（标准增量 → erp_document_items）

历史：
  2026-04-11 删除 sync_batch_stock 死代码（Bug 4）。原因：
  - 业务无保质期商品，erp_batch_stock 表 19 天 0 行数据
  - 函数本身有 NameError bug（len(shop_ids) 引用未定义变量）
  - except logger.debug 静默吞所有错误，无可观测性
  - 每天浪费 ~10000 次 API 调用挤占其他 sync 限流额度
  - ERP Agent 仍可通过 batch_stock_list 工具实时查 API（registry 保留）
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import (
    _API_SEM,
    _batch_upsert,
    _fmt_dt,
    _pick,
    _safe_ts,
)
from utils.time_context import now_cn

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
    now = now_cn().isoformat()

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

    # 按冲突键去重：同一批内可能有重复的 (system_id, operate_time, action)，
    # PG 单条 upsert 不允许同一行被更新两次
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for row in all_rows:
        key = (row["system_id"], row["operate_time"], row["action"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    all_rows = deduped

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
    now = now_cn().isoformat()

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
    now = now_cn().isoformat()

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
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
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
