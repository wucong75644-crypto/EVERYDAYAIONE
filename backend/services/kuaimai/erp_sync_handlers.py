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

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 工具函数 ────────────────────────────────────────────


def _fmt_dt(dt: datetime) -> str:
    """yyyy-MM-dd HH:mm:ss（采购/采退时间参数格式）"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_d(dt: datetime) -> str:
    """YYYY-MM-DD（收货/上架/售后/订单时间参数格式）"""
    return dt.strftime("%Y-%m-%d")


def _pick(src: dict, *keys: str) -> dict:
    """从 dict 中提取存在且非 None 的键值对（用于 extra_json）"""
    return {k: src[k] for k in keys if k in src and src[k] is not None}


def _to_float(val: Any) -> float:
    """安全转 float（用于折扣分摊计算）"""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


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

    all_rows: list[dict[str, Any]] = []
    for doc in docs:
        try:
            detail = await client.request_with_retry(
                "purchase.order.get", {"id": doc["id"]},
            )
        except Exception as e:
            logger.warning(f"Purchase detail failed | id={doc.get('id')} | error={e}")
            continue

        items = detail.get("items") or []
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
                "doc_created_at": doc.get("created"),
                "doc_modified_at": doc.get("modified"),
                "item_index": item["_item_index"],
                "outer_id": item.get("outerId"),
                "sku_outer_id": item.get("itemOuterId"),
                "item_name": item.get("title"),
                "quantity": item.get("purchaseNum"),
                "quantity_received": item.get("arrivedQuantity"),
                "price": item.get("price"),
                "amount": item.get("amount") or item.get("totalFee"),
                "supplier_name": detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "delivery_date": detail.get("deliveryDate"),
                "remark": detail.get("remark"),
                "extra_json": extra,
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 收货单 (receipt) ────────────────────────────────────


async def sync_receipt(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """收货单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "warehouse.entry.list.query",
        {"startModified": _fmt_d(start), "endModified": _fmt_d(end)},
    )
    if not docs:
        return 0

    all_rows: list[dict[str, Any]] = []
    for doc in docs:
        try:
            detail = await client.request_with_retry(
                "warehouse.entry.list.get", {"id": doc["id"]},
            )
        except Exception as e:
            logger.warning(f"Receipt detail failed | id={doc.get('id')} | error={e}")
            continue

        items = detail.get("items") or detail.get("details") or []
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
                "doc_created_at": doc.get("created"),
                "doc_modified_at": doc.get("modified"),
                "item_index": item["_item_index"],
                "outer_id": item.get("outerId"),
                "sku_outer_id": item.get("itemOuterId"),
                "item_name": item.get("title"),
                "quantity": item.get("quantity"),
                "price": item.get("price"),
                "amount": item.get("amount"),
                "supplier_name": detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "purchase_order_code": detail.get("purchaseOrderCode"),
                "extra_json": extra,
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 上架单 (shelf) ──────────────────────────────────────


async def sync_shelf(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """上架单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "erp.purchase.shelf.query",
        {"startModified": _fmt_d(start), "endModified": _fmt_d(end)},
    )
    if not docs:
        return 0

    all_rows: list[dict[str, Any]] = []
    for doc in docs:
        try:
            detail = await client.request_with_retry(
                "erp.purchase.shelf.get", {"id": doc["id"]},
            )
        except Exception as e:
            logger.warning(f"Shelf detail failed | id={doc.get('id')} | error={e}")
            continue

        items = detail.get("items") or detail.get("details") or []
        items = svc.sort_and_assign_index(items, "shelf")
        for item in items:
            all_rows.append({
                "doc_type": "shelf",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": doc.get("created"),
                "doc_modified_at": doc.get("modified"),
                "item_index": item["_item_index"],
                "outer_id": item.get("outerId"),
                "sku_outer_id": item.get("itemOuterId"),
                "item_name": item.get("title"),
                "quantity": item.get("quantity"),
                "warehouse_name": detail.get("warehouseName"),
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
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

    all_rows: list[dict[str, Any]] = []
    for doc in docs:
        try:
            detail = await client.request_with_retry(
                "purchase.return.list.get", {"id": doc["id"]},
            )
        except Exception as e:
            logger.warning(f"Return detail failed | id={doc.get('id')} | error={e}")
            continue

        items = detail.get("items") or []
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
                "doc_created_at": doc.get("gmCreate"),  # 设计文档：字段名为 gmCreate
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

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 售后工单 (aftersale) ────────────────────────────────


async def sync_aftersale(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """售后工单同步：list only（含嵌套 items），空 items 仍插一行"""
    docs = await svc.fetch_all_pages(
        "erp.aftersale.list.query",
        {
            "startModified": _fmt_d(start),
            "endModified": _fmt_d(end),
            "asVersion": 2,
        },
        page_size=200,
    )
    if not docs:
        return 0

    all_rows: list[dict[str, Any]] = []
    for doc in docs:
        doc_extra = _pick(
            doc, "goodStatus", "refundWarehouseName",
            "refundExpressCompany", "refundExpressId",
            "reissueSid", "platformId", "shortId",
        )
        doc_base = {
            "doc_type": "aftersale",
            "doc_id": str(doc["id"]),
            "doc_status": doc.get("status"),
            "doc_created_at": doc.get("created"),
            "shop_name": doc.get("shopName"),
            "platform": doc.get("source"),
            "order_no": doc.get("tid"),
            "aftersale_type": doc.get("afterSaleType"),
            "refund_money": doc.get("refundMoney"),
            "raw_refund_money": doc.get("rawRefundMoney"),
            "text_reason": doc.get("textReason"),
            "finished_at": doc.get("finished"),
            "remark": doc.get("remark"),
        }

        items = doc.get("items") or []
        if not items:
            # 仅退款（type=1,5）无商品行，仍插一行保证工单不丢失
            all_rows.append({**doc_base, "item_index": 0, "extra_json": doc_extra})
            continue

        items = svc.sort_and_assign_index(items, "aftersale")
        for item in items:
            item_extra = _pick(item, "goodItemCount", "badItemCount", "type")
            merged_extra = {**doc_extra, **item_extra} if item_extra else doc_extra
            all_rows.append({
                **doc_base,
                "item_index": item["_item_index"],
                "outer_id": item.get("mainOuterId"),
                "sku_outer_id": item.get("outerId"),
                "item_name": item.get("title"),
                "quantity": item.get("receivableCount"),
                "real_qty": item.get("itemRealQty"),
                "price": item.get("price"),
                "amount": item.get("payment"),
                "extra_json": merged_extra,
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 销售订单 (order) ────────────────────────────────────


async def sync_order(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """订单同步：list only（含 orders[]），discountFee 按 payment 比例分摊"""
    docs = await svc.fetch_all_pages(
        "erp.trade.list.query",
        {
            "startTime": _fmt_d(start),
            "endTime": _fmt_d(end),
            "timeType": "upd_time",
        },
        page_size=200,
    )
    if not docs:
        return 0

    all_rows: list[dict[str, Any]] = []
    for doc in docs:
        items = doc.get("orders") or []
        if not items:
            continue
        items = svc.sort_and_assign_index(items, "order")

        total_discount = _to_float(doc.get("discountFee"))
        total_payment = sum(_to_float(i.get("payment")) for i in items) or 1
        doc_extra = _pick(
            doc, "type", "payAmount",
            "isCancel", "isRefund", "isExcep", "isHalt", "isUrgent",
        )

        discount_used = 0.0
        for item in items:
            idx = item["_item_index"]
            payment = _to_float(item.get("payment"))
            # 折扣按 payment 比例分摊，末项用差值兜底精度（设计文档 §7.1）
            if idx < len(items) - 1:
                item_discount = round(total_discount * payment / total_payment, 2)
                discount_used += item_discount
            else:
                item_discount = round(total_discount - discount_used, 2)

            all_rows.append({
                "doc_type": "order",
                "doc_id": str(doc.get("sid", "")),
                "doc_status": doc.get("sysStatus"),
                "doc_created_at": doc.get("created"),
                "doc_modified_at": doc.get("modified"),
                "item_index": idx,
                "outer_id": item.get("sysOuterId"),
                "sku_outer_id": item.get("outerSkuId"),
                "item_name": item.get("title"),
                "quantity": item.get("num"),
                "price": item.get("price"),
                "amount": item.get("payment"),
                "cost": item.get("cost"),
                "refund_status": item.get("refundStatus"),
                "discount_fee": item_discount if total_discount else None,
                "post_fee": doc.get("postFee") if idx == 0 else None,
                "gross_profit": doc.get("grossProfit") if idx == 0 else None,
                "order_no": doc.get("tid"),
                "order_status": doc.get("sysStatus"),
                "express_no": doc.get("outSid"),
                "express_company": doc.get("expressCompanyName"),
                "shop_name": doc.get("shopName"),
                "platform": doc.get("source"),
                "warehouse_name": doc.get("warehouseName"),
                "pay_time": doc.get("payTime"),
                "consign_time": doc.get("consignTime"),
                "remark": doc.get("sellerMemo"),
                "sys_memo": doc.get("sysMemo"),
                "buyer_message": doc.get("buyerMessage"),
                "extra_json": {**doc_extra, "payment": item.get("payment")},
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count
