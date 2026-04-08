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

from services.kuaimai.erp_sync_utils import (  # noqa: F401 — re-export for backward compat
    _API_SEM,
    _DetailResult,
    _fetch_details,
    _fmt_d,
    _fmt_dt,
    _pick,
    _safe_ts,
    _to_float,
)

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


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

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "purchase")
        extra = _pick(
            detail, "shortId", "totalAmount", "actualTotalAmount",
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
                "price": item.get("price"),
                "amount": item.get("amount") or item.get("totalFee"),
                "supplier_name": item.get("supplierName") or detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "delivery_date": _safe_ts(item.get("deliveryDate") or detail.get("deliveryDate")),
                "remark": doc.get("remark"),
                "extra_json": extra,
            })

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

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "receipt")
        extra = _pick(
            detail, "shelvedQuantity", "getGoodNum", "getBadNum",
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
                "price": item.get("price"),
                "amount": item.get("amount"),
                "supplier_name": detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "purchase_order_code": detail.get("purchaseOrderCode"),
                "extra_json": extra,
            })

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
                "warehouse_name": detail.get("warehouseName"),
            })

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

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "purchase_return")
        extra = _pick(
            detail, "shortId", "totalAmount", "financeStatus",
            "statusName", "tagName",
        )
        # 采退单 purchaseOrderId 是数字 ID，转为字符串存储
        po_id = detail.get("purchaseOrderId")
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
                "price": item.get("price"),
                "amount": item.get("amount"),
                "supplier_name": detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "purchase_order_code": po_code,
                "extra_json": extra,
            })

    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 售后工单 (aftersale) ────────────────────────────────


def _build_aftersale_rows(
    doc: dict, svc: ErpSyncService,
) -> list[dict[str, Any]]:
    """从单个售后 doc 构建 DB 行（供 sync_aftersale 和对账共用）"""
    doc_extra = _pick(
        doc, "goodStatus", "refundWarehouseName",
        "refundExpressCompany", "refundExpressId",
        "reissueSid", "platformId", "shortId",
    )
    doc_base = {
        "doc_type": "aftersale",
        "doc_id": str(doc["id"]),
        "doc_status": doc.get("status"),
        "doc_created_at": _safe_ts(doc.get("created")),
        "doc_modified_at": _safe_ts(doc.get("modified")),
        "shop_name": doc.get("shopName"),
        "platform": doc.get("source"),
        "order_no": doc.get("tid"),
        "aftersale_type": doc.get("afterSaleType"),
        "refund_money": doc.get("refundMoney"),
        "raw_refund_money": doc.get("rawRefundMoney"),
        "text_reason": doc.get("textReason"),
        "finished_at": _safe_ts(doc.get("finished")),
        "remark": doc.get("remark"),
        "good_status": doc.get("goodStatus"),
        "refund_warehouse_name": doc.get("refundWarehouseName"),
        "refund_express_company": doc.get("refundExpressCompany"),
        "refund_express_no": doc.get("refundExpressId"),
        "reissue_sid": doc.get("reissueSid"),
        "platform_refund_id": doc.get("platformId"),
        "short_id": doc.get("shortId"),
    }

    items = doc.get("items") or []
    if not items:
        return [{**doc_base, "item_index": 0, "extra_json": doc_extra}]

    items = svc.sort_and_assign_index(items, "aftersale")
    rows: list[dict[str, Any]] = []
    for item in items:
        item_extra = _pick(item, "goodItemCount", "badItemCount", "type")
        merged_extra = {**doc_extra, **item_extra} if item_extra else doc_extra
        rows.append({
            **doc_base,
            "item_index": item["_item_index"],
            "outer_id": item.get("mainOuterId"),
            "sku_outer_id": item.get("outerId"),
            "item_name": item.get("title"),
            "quantity": item.get("receivableCount"),
            "real_qty": item.get("itemRealQty"),
            "price": item.get("price"),
            "amount": item.get("payment"),
            "good_item_count": item.get("goodItemCount"),
            "bad_item_count": item.get("badItemCount"),
            "extra_json": merged_extra,
        })
    return rows


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


def _build_order_rows(
    doc: dict, svc: ErpSyncService,
) -> list[dict[str, Any]]:
    """从单个订单 doc 构建 DB 行（供 sync_order 和死信重试共用）"""
    items = doc.get("orders") or []
    if not items:
        return []
    items = svc.sort_and_assign_index(items, "order")

    total_discount = _to_float(doc.get("discountFee"))
    total_payment = sum(_to_float(i.get("payment")) for i in items) or 1
    doc_extra = _pick(
        doc, "type", "payAmount",
        "isCancel", "isRefund", "isExcep", "isHalt", "isUrgent",
    )

    rows: list[dict[str, Any]] = []
    discount_used = 0.0
    for pos, item in enumerate(items):
        payment = _to_float(item.get("payment"))
        is_last = (pos == len(items) - 1)
        if not is_last:
            item_discount = round(total_discount * payment / total_payment, 2)
            discount_used += item_discount
        else:
            item_discount = round(total_discount - discount_used, 2)

        rows.append({
            "doc_type": "order",
            "doc_id": str(doc.get("sid", "")),
            "doc_status": doc.get("sysStatus"),
            "doc_created_at": _safe_ts(doc.get("created")),
            "doc_modified_at": _safe_ts(doc.get("modified")),
            "item_index": item["_item_index"],
            "outer_id": item.get("sysItemOuterId"),   # 主编码
            "sku_outer_id": item.get("sysOuterId"),    # SKU编码
            "item_name": item.get("title"),
            "quantity": item.get("num"),
            "price": item.get("price"),
            "amount": item.get("payment"),
            "cost": item.get("cost"),
            "refund_status": item.get("refundStatus"),
            "discount_fee": item_discount if total_discount else None,
            "post_fee": doc.get("postFee") if pos == 0 else None,
            "gross_profit": doc.get("grossProfit") if pos == 0 else None,
            "order_no": doc.get("tid"),
            "order_status": doc.get("sysStatus"),
            "express_no": doc.get("outSid"),
            "express_company": doc.get("expressCompanyName"),
            "shop_name": doc.get("shopName"),
            "platform": doc.get("source"),
            "warehouse_name": doc.get("warehouseName"),
            "pay_time": _safe_ts(doc.get("payTime")),
            "consign_time": _safe_ts(doc.get("consignTime")),
            "remark": doc.get("sellerMemo"),
            "sys_memo": doc.get("sysMemo"),
            "buyer_message": doc.get("buyerMessage"),
            "order_type": doc.get("type"),
            "pay_amount": doc.get("payAmount"),
            "is_cancel": doc.get("isCancel"),
            "is_refund": doc.get("isRefund"),
            "is_exception": doc.get("isExcep"),
            "is_halt": doc.get("isHalt"),
            "is_urgent": doc.get("isUrgent"),
            "extra_json": {**doc_extra, "payment": item.get("payment")},
        })
    return rows


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

    # 已发货/已完成的 sids（搭便车 express 只查这些）
    shipped_sids: set[str] = set()
    _SHIPPED_STATUSES = {"SELLER_SEND_GOODS", "FINISHED"}

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
                if doc.get("sysStatus") in _SHIPPED_STATUSES:
                    shipped_sids.add(sid)

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

    # 搭便车：订单操作日志（全部 sids）+ 包裹信息（仅已发货）
    if seen_sids:
        try:
            from services.kuaimai.erp_sync_piggyback_handlers import (
                piggyback_express,
                piggyback_order_log,
            )
            await piggyback_order_log(svc, list(seen_sids))
            if shipped_sids:
                await piggyback_express(svc, list(shipped_sids))
        except Exception as e:
            logger.warning(f"Order piggyback failed (non-fatal) | error={e}")

    return total_count
