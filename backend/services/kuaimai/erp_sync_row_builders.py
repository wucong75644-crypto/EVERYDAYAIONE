"""
ERP 同步行构建器——订单 & 售后的字段映射

从 erp_sync_handlers.py 提取，供 sync_order / sync_aftersale / 死信重试 / 对账共用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import _pick, _safe_ts, _to_float

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


def _build_aftersale_rows(
    doc: dict, svc: ErpSyncService,
) -> list[dict[str, Any]]:
    """从单个售后 doc 构建 DB 行（供 sync_aftersale 和对账共用）"""
    doc_extra = _pick(
        doc, "goodStatus", "refundWarehouseName",
        "refundExpressCompany", "refundExpressId",
        "reissueSid", "platformId", "shortId",
    )

    # message_memos JSONB 防御性校验
    raw_memos = doc.get("messageMemos")
    if raw_memos is not None and not isinstance(raw_memos, list):
        logger.warning(
            f"message_memos unexpected type | "
            f"doc_id={doc.get('id')} type={type(raw_memos).__name__}"
        )
        raw_memos = None

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
        # ── 081 新增：售后头级别 ──
        "order_sid": doc.get("orderSid"),
        "reason": doc.get("reason"),
        "order_type_ref": doc.get("orderType"),
        "buyer_name": doc.get("buyerName"),
        "buyer_phone": doc.get("buyerPhone"),
        "wangwang_num": doc.get("wangwangNum"),
        "apply_date": _safe_ts(doc.get("applyDate")),
        "after_sale_app_time": _safe_ts(doc.get("afterSaleAppTime")),
        "platform_complete_time": _safe_ts(doc.get("platformCompleteTime")),
        "online_status": doc.get("onlineStatus"),
        "online_status_text": doc.get("onlineStatusText"),
        "platform_status": doc.get("platformStatus"),
        "handler_status": doc.get("handlerStatus"),
        "handler_status_text": doc.get("handlerStatusText"),
        "deal_result": doc.get("dealResult"),
        "advance_status": doc.get("advanceStatus"),
        "advance_status_text": doc.get("advanceStatusText"),
        "dest_work_order_status": doc.get("destWorkOrderStatus"),
        "storage_progress": doc.get("storageProgress"),
        "refund_warehouse_id": doc.get("refundWarehouseId"),
        "trade_warehouse_name": doc.get("tradeWarehouseName"),
        "message_memos": raw_memos,
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
            # ── 081 新增：售后子项级别 ──
            "item_refund_money": item.get("refundMoney"),
            "item_raw_refund_money": item.get("rawRefundMoney"),
            "refundable_money": item.get("refundableMoney"),
            "properties_name": item.get("propertiesName"),
            "item_pic_path": item.get("picPath"),
            "receive_goods_time": _safe_ts(item.get("receiveGoodsTime")),
            "item_detail_id": item.get("detailId"),
            "item_snapshot_id": item.get("snapshotId"),
            "num_iid": item.get("numIid"),
            "sku_id": item.get("skuId"),
            "is_gift": item.get("isGift", 0),
            "is_match": item.get("isMatch", 0),
            "suite": item.get("suite", 0),
            "suite_type": item.get("suiteType", 0),
            "extra_json": merged_extra,
        })
    return rows


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
    doc_extra = _pick(doc, "payment")

    # ── tradeTags JSONB 防御性校验 ──
    raw_tags = doc.get("tradeTags")
    if raw_tags is not None and not isinstance(raw_tags, list):
        logger.warning(
            f"trade_tags unexpected type | "
            f"doc_id={doc.get('sid')} type={type(raw_tags).__name__} val={str(raw_tags)[:200]}"
        )
        raw_tags = None

    # exceptions → TEXT[] 防御性校验
    raw_exceptions = doc.get("exceptions")
    if raw_exceptions is not None and not isinstance(raw_exceptions, list):
        logger.warning(
            f"exception_tags unexpected type | "
            f"doc_id={doc.get('sid')} type={type(raw_exceptions).__name__}"
        )
        raw_exceptions = None
    # PostgreSQL TEXT[] 需要 {val1,val2} 格式，不能用 JSON ["val1","val2"]
    if raw_exceptions:
        parts = [str(e).replace('"', '\\"') for e in raw_exceptions]
        exception_tags = "{" + ",".join(f'"{p}"' for p in parts) + "}"
    else:
        exception_tags = None

    # trade_invoice JSONB 防御性校验
    raw_invoice = doc.get("invoice")
    if raw_invoice is not None and not isinstance(raw_invoice, dict):
        raw_invoice = None

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

        is_first = pos == 0

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
            "post_fee": doc.get("postFee") if is_first else None,
            "gross_profit": doc.get("grossProfit") if is_first else None,
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
            # 标记字段（独立列）
            "order_type": doc.get("type"),
            "pay_amount": doc.get("payAmount"),
            "is_cancel": doc.get("isCancel"),
            "is_refund": doc.get("isRefund"),
            "is_exception": doc.get("isExcep"),
            "is_halt": doc.get("isHalt"),
            "is_urgent": doc.get("isUrgent"),
            # 买家 + 收件人（订单头级别，仅首行存储避免冗余）
            "buyer_nick": doc.get("buyerNick") if is_first else None,
            "receiver_name": doc.get("receiverName") if is_first else None,
            "receiver_mobile": doc.get("receiverMobile") if is_first else None,
            "receiver_phone": doc.get("receiverPhone") if is_first else None,
            "receiver_state": doc.get("receiverState") if is_first else None,
            "receiver_city": doc.get("receiverCity") if is_first else None,
            "receiver_district": doc.get("receiverDistrict") if is_first else None,
            "receiver_address": doc.get("receiverAddress") if is_first else None,
            "status_name": doc.get("statusName"),
            # ── 081 新增：订单头级别 ──
            "trade_tags": raw_tags,
            "exception_tags": exception_tags,
            "is_scalping": doc.get("scalping", 0),
            "total_fee": doc.get("totalFee"),
            "ac_payment": doc.get("acPayment"),
            "actual_post_fee": doc.get("actualPostFee"),
            "theory_post_fee": doc.get("theoryPostFee"),
            "sale_fee": doc.get("saleFee"),
            "sale_price": doc.get("salePrice"),
            "packma_cost": doc.get("packmaCost"),
            "unified_status": doc.get("unifiedStatus"),
            "stock_status": doc.get("stockStatus"),
            "is_handler_memo": doc.get("isHandlerMemo"),
            "is_handler_message": doc.get("isHandlerMessage"),
            "is_package": doc.get("isPackage"),
            "is_presell": doc.get("isPresell"),
            "seller_flag": doc.get("sellerFlag"),
            "belong_type": doc.get("belongType"),
            "convert_type": doc.get("convertType"),
            "express_status": doc.get("expressStatus"),
            "deliver_status": doc.get("deliverStatus"),
            "audit_time": _safe_ts(doc.get("auditTime")),
            "timeout_action_time": _safe_ts(doc.get("timeoutActionTime")),
            "deliver_print_time": _safe_ts(doc.get("deliverPrintTime")),
            "express_print_time": _safe_ts(doc.get("expressPrintTime")),
            "end_time": _safe_ts(doc.get("endTime")),
            "pt_consign_time": _safe_ts(doc.get("ptConsignTime")),
            "weight": doc.get("weight"),
            "net_weight": doc.get("netWeight"),
            "volume": doc.get("volume"),
            "template_name": doc.get("templateName"),
            "warehouse_id": doc.get("warehouseId"),
            "split_sid": doc.get("splitSid"),
            "split_type": doc.get("splitType"),
            "item_num": doc.get("itemNum"),
            "item_kind_num": doc.get("itemKindNum"),
            "receiver_street": doc.get("receiverStreet") if is_first else None,
            "trade_invoice": raw_invoice,
            # ── 081 新增：订单子项级别 ──
            "item_discount_fee": item.get("discountFee"),
            "item_discount_rate": item.get("discountRate"),
            "item_ac_payment": item.get("acPayment"),
            "item_total_fee": item.get("totalFee"),
            "divide_order_fee": item.get("divideOrderFee"),
            "sku_properties_name": item.get("skuPropertiesName") or item.get("propertiesName"),
            "sys_title": item.get("sysTitle"),
            "sys_sku_properties_name": item.get("sysSkuPropertiesName"),
            "pic_path": item.get("picPath"),
            "sys_pic_path": item.get("sysPicPath"),
            "suits": item.get("suits") if isinstance(item.get("suits"), list) else None,
            "order_ext": item.get("orderExt") if isinstance(item.get("orderExt"), dict) else None,
            "gift_num": item.get("giftNum", 0),
            "stock_num": item.get("stockNum"),
            "item_net_weight": item.get("netWeight"),
            "insufficient_canceled": item.get("insufficientCanceled", 0),
            "item_is_cancel": item.get("isCancel", 0),
            "item_is_presell": item.get("isPresell", 0),
            "is_virtual": item.get("isVirtual", 0),
            "estimate_con_time": _safe_ts(item.get("estimateConTime")),
            "diff_stock_num": item.get("diffStockNum"),
            "extra_json": {**doc_extra, "payment": item.get("payment")},
        })
    return rows
