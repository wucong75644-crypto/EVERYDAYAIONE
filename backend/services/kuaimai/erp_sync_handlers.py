"""
ERP 单据同步处理器（6种单据类型）

purchase / receipt / shelf / purchase_return / aftersale / order
每个处理器：list→(detail)→字段映射→upsert→聚合

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_row_builders import (  # noqa: F401
    _build_aftersale_rows,
    _build_order_rows,
)
from services.kuaimai.erp_sync_utils import (  # noqa: F401 — re-export for backward compat
    _API_SEM,
    _DetailResult,
    _fen_to_yuan,
    _fetch_details,
    _fmt_d,
    _fmt_dt,
    _pick,
    _pick_money,
    _safe_ts,
    _to_float,
)

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


async def _backfill_item_names(
    svc: "ErpSyncService", rows: list[dict[str, Any]],
) -> None:
    """批量回填 item_name：快麦采购类 API 不返回 title，用 erp_products.title 补全。"""
    missing = {
        r["outer_id"] for r in rows
        if not r.get("item_name") and r.get("outer_id")
    }
    if not missing:
        return

    try:
        result = svc.db.table("erp_products").select(
            "outer_id, title",
        ).in_("outer_id", list(missing))
        if svc.org_id:
            result = result.eq("org_id", svc.org_id)
        data = (await result.execute()).data
        name_map = {r["outer_id"]: r["title"] for r in data if r.get("title")}
    except Exception as e:
        logger.warning(f"backfill_item_names 查询失败，跳过 | error={e}")
        return

    filled = 0
    for r in rows:
        if not r.get("item_name") and r.get("outer_id") in name_map:
            r["item_name"] = name_map[r["outer_id"]]
            filled += 1
    if filled:
        logger.info(f"backfill_item_names | filled={filled}/{len(rows)}")


# ── 采购单 (purchase) ──────────────────────────────────


async def sync_purchase(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """采购单同步：list + detail，items 按 outerId+itemOuterId 排序"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "purchase.order.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "purchase.order.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(svc.db, "purchase", "purchase.order.get", detail_result.failed, org_id=svc.org_id)

    # 快麦采购 API 金额以"分"返回，入库统一转"元"
    _PURCHASE_MONEY_KEYS = {"totalAmount", "actualTotalAmount", "totalFee", "amendAmount"}
    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "purchase")
        extra = _pick_money(
            doc, _PURCHASE_MONEY_KEYS,
            "shortId", "totalAmount", "actualTotalAmount",
            "financeStatus", "arrivedQuantity", "receiveQuantity",
            "totalFee", "amendAmount",
        )
        for item in items:
            all_rows.append({
                "doc_type": "purchase",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),      # itemOuterId=主编码
                "sku_outer_id": item.get("outerId"),      # outerId=SKU编码
                "item_name": item.get("title"),
                "quantity": item.get("count"),
                "quantity_received": item.get("arrivedQuantity"),
                "price": _fen_to_yuan(item.get("price")),
                "amount": _fen_to_yuan(item.get("amount") or item.get("totalFee")),
                "supplier_name": doc.get("supplierName"),
                "supplier_code": item.get("supplierCode") or doc.get("supplierCode"),
                "warehouse_name": doc.get("receiveWarehouseName"),
                "warehouse_id": str(doc["receiveWarehouseId"]) if doc.get("receiveWarehouseId") else None,
                "creator_name": doc.get("createrName"),
                "delivery_date": _safe_ts(item.get("deliveryDate")),
                "remark": doc.get("remark"),
                "extra_json": extra,
            })

    await _backfill_item_names(svc, all_rows)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 收货单 (receipt) ────────────────────────────────────


async def sync_receipt(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """收货单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "warehouse.entry.list.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "warehouse.entry.list.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(svc.db, "receipt", "warehouse.entry.list.get", detail_result.failed, org_id=svc.org_id)

    # 快麦收货 API 金额以"分"返回，入库统一转"元"
    _RECEIPT_MONEY_KEYS = {"totalDetailFee"}
    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "receipt")
        extra = _pick_money(
            doc, _RECEIPT_MONEY_KEYS,
            "shelvedQuantity", "getGoodNum", "getBadNum",
            "totalDetailFee", "busyTypeDesc",
        )
        for item in items:
            all_rows.append({
                "doc_type": "receipt",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),      # itemOuterId=主编码
                "sku_outer_id": item.get("outerId"),      # outerId=SKU编码
                "item_name": item.get("title"),
                "quantity": item.get("count"),
                "price": _fen_to_yuan(item.get("price")),
                "amount": _fen_to_yuan(item.get("amount")),
                "supplier_name": item.get("supplierName") or doc.get("supplierName"),
                "supplier_code": item.get("supplierCode") or doc.get("supplierCode"),
                "warehouse_name": doc.get("warehouseName"),
                "warehouse_id": str(doc["warehouseId"]) if doc.get("warehouseId") else None,
                "creator_name": doc.get("createrName"),
                "purchase_order_code": doc.get("purchaseOrderCode"),
                "extra_json": extra,
            })

    await _backfill_item_names(svc, all_rows)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 上架单 (shelf) ──────────────────────────────────────


async def sync_shelf(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """上架单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "erp.purchase.shelf.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "erp.purchase.shelf.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(svc.db, "shelf", "erp.purchase.shelf.get", detail_result.failed, org_id=svc.org_id)

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "shelf")
        for item in items:
            all_rows.append({
                "doc_type": "shelf",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),      # itemOuterId=主编码
                "sku_outer_id": item.get("outerId"),      # outerId=SKU编码
                "item_name": item.get("title"),
                "quantity": item.get("count"),
                "price": _fen_to_yuan(item.get("price")),
                "warehouse_name": doc.get("warehouseName"),
                "warehouse_id": str(doc["warehouseId"]) if doc.get("warehouseId") else None,
                "supplier_name": doc.get("supplierName"),
                "supplier_code": svc.resolve_supplier_code(doc.get("supplierName")),
                "creator_name": doc.get("creator"),        # shelf 用 creator 非 createrName
                "purchase_order_code": doc.get("weCode"),  # 关联收货单号
            })

    await _backfill_item_names(svc, all_rows)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 采退单 (purchase_return) ────────────────────────────


async def sync_purchase_return(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """采购退货单同步：list + detail（注意 gmCreate / 编码映射反转）"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "purchase.return.list.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "purchase.return.list.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(svc.db, "purchase_return", "purchase.return.list.get", detail_result.failed, org_id=svc.org_id)

    # 快麦采退 API 金额以"分"返回，入库统一转"元"
    _RETURN_MONEY_KEYS = {"totalAmount"}
    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "purchase_return")
        extra = _pick_money(
            doc, _RETURN_MONEY_KEYS,
            "shortId", "totalAmount", "financeStatus",
            "statusName", "tagName",
        )
        # 采退单 purchaseOrderId 是数字 ID，转为字符串存储
        po_id = doc.get("purchaseOrderId")
        po_code = str(po_id) if po_id is not None else None
        for item in items:
            all_rows.append({
                "doc_type": "purchase_return",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": str(doc.get("status", "")),
                "doc_created_at": _safe_ts(doc.get("gmCreate")),  # 设计文档：字段名为 gmCreate
                "doc_modified_at": _safe_ts(doc.get("modified") or doc.get("gmModified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),     # 设计文档：itemOuterId→outer_id
                "sku_outer_id": item.get("outerId"),     # 设计文档：outerId→sku_outer_id
                "item_name": item.get("title"),
                "quantity": item.get("returnNum"),
                "actual_return_qty": item.get("actualReturnNum"),
                "price": _fen_to_yuan(item.get("price")),
                "amount": _fen_to_yuan(item.get("amount")),
                "supplier_name": item.get("supplierName") or doc.get("supplierName"),
                "supplier_code": item.get("supplierCode") or doc.get("supplierCode"),
                "warehouse_name": doc.get("warehouseName"),
                "warehouse_id": str(doc["warehouseId"]) if doc.get("warehouseId") else None,
                "creator_name": doc.get("createrName"),
                "purchase_order_code": po_code,
                "extra_json": extra,
            })

    await _backfill_item_names(svc, all_rows)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 售后工单 (aftersale) ────────────────────────────────


async def sync_aftersale(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """售后工单同步：流式拉取 + 每1000条刷库，避免内存堆积"""
    flush_size = svc.FLUSH_THRESHOLD
    all_rows: list[dict[str, Any]] = []
    affected_key_set: set[tuple[str, str]] = set()
    total_count = 0
    seen_wids: set[str] = set()  # 收集工单 ID 用于搭便车

    async for page_docs in svc.fetch_pages_streaming(
        "erp.aftersale.list.query",
        {
            "startModified": _fmt_dt(start),
            "endModified": _fmt_dt(end),
            "asVersion": 2,
        },
        page_size=200,
    ):
        for doc in page_docs:
            wid = doc.get("id")
            if wid:
                seen_wids.add(str(wid))
            all_rows.extend(_build_aftersale_rows(doc, svc))

        # 每 1000 条刷一次库，释放内存
        if len(all_rows) >= flush_size:
            total_count += await svc.upsert_document_items(all_rows)
            affected_key_set.update(svc.collect_affected_keys(all_rows))
            all_rows.clear()

    # 剩余数据刷库
    if all_rows:
        total_count += await svc.upsert_document_items(all_rows)
        affected_key_set.update(svc.collect_affected_keys(all_rows))

    await svc.run_aggregation(list(affected_key_set))

    # 搭便车：售后操作日志
    if seen_wids:
        try:
            from services.kuaimai.erp_sync_piggyback_handlers import piggyback_aftersale_log
            await piggyback_aftersale_log(svc, list(seen_wids))
        except Exception as e:
            logger.warning(f"Aftersale log piggyback failed (non-fatal) | error={e}")

    return total_count


# ── 销售订单 (order) ────────────────────────────────────

# 订单同步的时间维度：upd_time 拉变更，pay_time 补漏
# （部分订单 modified=None，upd_time 查不到，pay_time 兜底）
_ORDER_TIME_TYPES = ["upd_time", "pay_time"]


async def sync_order(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """订单同步：双维度拉取（upd_time + pay_time），upsert 自动去重"""
    flush_size = svc.FLUSH_THRESHOLD
    all_rows: list[dict[str, Any]] = []
    affected_key_set: set[tuple[str, str]] = set()
    total_count = 0
    seen_sids: set[str] = set()  # 跨维度去重

    for time_type in _ORDER_TIME_TYPES:
        async for page_docs in svc.fetch_pages_streaming(
            "erp.trade.outstock.simple.query",
            {
                "startTime": _fmt_dt(start),
                "endTime": _fmt_dt(end),
                "timeType": time_type,
            },
            page_size=200,
        ):
            for doc in page_docs:
                sid = str(doc.get("sid", ""))
                if sid in seen_sids:
                    continue  # 跨维度去重，同一订单只处理一次
                seen_sids.add(sid)

                rows = _build_order_rows(doc, svc)
                all_rows.extend(rows)

            # 每 1000 条刷一次库，释放内存
            if len(all_rows) >= flush_size:
                total_count += await svc.upsert_document_items(all_rows)
                affected_key_set.update(svc.collect_affected_keys(all_rows))
                all_rows.clear()

    # 剩余数据刷库
    if all_rows:
        total_count += await svc.upsert_document_items(all_rows)
        affected_key_set.update(svc.collect_affected_keys(all_rows))

    await svc.run_aggregation(list(affected_key_set))

    # 搭便车：订单操作日志
    # express 搭便车已禁用（多包裹极少，单包裹快递信息订单同步已存）
    if seen_sids:
        try:
            from services.kuaimai.erp_sync_piggyback_handlers import piggyback_order_log
            await piggyback_order_log(svc, list(seen_sids))
        except Exception as e:
            logger.warning(f"Order piggyback failed (non-fatal) | error={e}")

    return total_count
