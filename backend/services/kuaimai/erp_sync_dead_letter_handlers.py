"""
死信重试时的行构建逻辑

从 erp_sync_handlers.py 中各 handler 的行构建逻辑提取，
死信消费者拿到 detail 后调用此模块构建 DB 行。
"""

from __future__ import annotations

from typing import Any

from services.kuaimai.erp_sync_handlers import _pick, _safe_ts
from services.kuaimai.erp_sync_service import ErpSyncService


def build_rows_from_detail(
    doc_type: str, doc: dict, detail: dict,
) -> list[dict[str, Any]]:
    """根据 doc_type 分发到对应的行构建函数"""
    builder = _BUILDERS.get(doc_type)
    if builder is None:
        return []
    return builder(doc, detail)


def _build_purchase(doc: dict, detail: dict) -> list[dict[str, Any]]:
    items = detail.get("list") or []
    items = ErpSyncService.sort_and_assign_index(items, "purchase")
    extra = _pick(
        detail, "shortId", "totalAmount", "actualTotalAmount",
        "financeStatus", "arrivedQuantity", "receiveQuantity",
        "totalFee", "amendAmount",
    )
    rows = []
    for item in items:
        rows.append({
            "doc_type": "purchase",
            "doc_id": str(doc["id"]),
            "doc_code": doc.get("code"),
            "doc_status": doc.get("status"),
            "doc_created_at": _safe_ts(doc.get("created")),
            "doc_modified_at": _safe_ts(doc.get("modified")),
            "item_index": item["_item_index"],
            "outer_id": item.get("itemOuterId"),
            "sku_outer_id": item.get("outerId"),
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
    return rows


def _build_receipt(doc: dict, detail: dict) -> list[dict[str, Any]]:
    items = detail.get("list") or []
    items = ErpSyncService.sort_and_assign_index(items, "receipt")
    extra = _pick(
        detail, "shelvedQuantity", "getGoodNum", "getBadNum",
        "totalDetailFee", "busyTypeDesc",
    )
    rows = []
    for item in items:
        rows.append({
            "doc_type": "receipt",
            "doc_id": str(doc["id"]),
            "doc_code": doc.get("code"),
            "doc_status": doc.get("status"),
            "doc_created_at": _safe_ts(doc.get("created")),
            "doc_modified_at": _safe_ts(doc.get("modified")),
            "item_index": item["_item_index"],
            "outer_id": item.get("itemOuterId"),
            "sku_outer_id": item.get("outerId"),
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
    return rows


def _build_shelf(doc: dict, detail: dict) -> list[dict[str, Any]]:
    items = detail.get("list") or []
    items = ErpSyncService.sort_and_assign_index(items, "shelf")
    rows = []
    for item in items:
        rows.append({
            "doc_type": "shelf",
            "doc_id": str(doc["id"]),
            "doc_code": doc.get("code"),
            "doc_status": doc.get("status"),
            "doc_created_at": _safe_ts(doc.get("created")),
            "doc_modified_at": _safe_ts(doc.get("modified")),
            "item_index": item["_item_index"],
            "outer_id": item.get("itemOuterId"),
            "sku_outer_id": item.get("outerId"),
            "item_name": item.get("title"),
            "quantity": item.get("count"),
            "warehouse_name": detail.get("warehouseName"),
        })
    return rows


def _build_purchase_return(doc: dict, detail: dict) -> list[dict[str, Any]]:
    items = detail.get("list") or []
    items = ErpSyncService.sort_and_assign_index(items, "purchase_return")
    extra = _pick(
        detail, "shortId", "totalAmount", "financeStatus",
        "statusName", "tagName",
    )
    po_id = detail.get("purchaseOrderId")
    po_code = str(po_id) if po_id is not None else None
    rows = []
    for item in items:
        rows.append({
            "doc_type": "purchase_return",
            "doc_id": str(doc["id"]),
            "doc_code": doc.get("code"),
            "doc_status": str(doc.get("status", "")),
            "doc_created_at": _safe_ts(doc.get("gmCreate")),
            "doc_modified_at": _safe_ts(doc.get("modified") or doc.get("gmModified")),
            "item_index": item["_item_index"],
            "outer_id": item.get("itemOuterId"),
            "sku_outer_id": item.get("outerId"),
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
    return rows


_BUILDERS: dict[str, Any] = {
    "purchase": _build_purchase,
    "receipt": _build_receipt,
    "shelf": _build_shelf,
    "purchase_return": _build_purchase_return,
}
