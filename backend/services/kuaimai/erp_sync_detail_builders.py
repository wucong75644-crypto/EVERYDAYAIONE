"""Shared normal/recovery mappings; source cents are normalized exactly once."""
from services.kuaimai.erp_sync_persistence import sort_and_assign_index
from services.kuaimai.erp_sync_utils import _safe_ts, _pick_money, _fen_to_yuan

def build_purchase(doc, detail, supplier_code=None):
    _PURCHASE_MONEY_KEYS = {"totalAmount", "actualTotalAmount", "totalFee", "amendAmount"}
    rows = []
    items = detail.get("list") or []
    items = sort_and_assign_index(items, "purchase")
    extra = _pick_money(
        doc, _PURCHASE_MONEY_KEYS,
        "shortId", "totalAmount", "actualTotalAmount",
        "financeStatus", "arrivedQuantity", "receiveQuantity",
        "totalFee", "amendAmount",
    )
    for item in items:
        rows.append({
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

    return rows


def build_receipt(doc, detail, supplier_code=None):
    _RECEIPT_MONEY_KEYS = {"totalDetailFee"}
    rows = []
    items = detail.get("list") or []
    items = sort_and_assign_index(items, "receipt")
    extra = _pick_money(
        doc, _RECEIPT_MONEY_KEYS,
        "shelvedQuantity", "getGoodNum", "getBadNum",
        "totalDetailFee", "busyTypeDesc",
    )
    for item in items:
        rows.append({
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

    return rows


def build_shelf(doc, detail, supplier_code=None):
    rows = []
    items = detail.get("list") or []
    items = sort_and_assign_index(items, "shelf")
    for item in items:
        rows.append({
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
            "supplier_code": supplier_code,
            "creator_name": doc.get("creator"),        # shelf 用 creator 非 createrName
            "purchase_order_code": doc.get("weCode"),  # 关联收货单号
        })

    return rows


def build_purchase_return(doc, detail, supplier_code=None):
    _RETURN_MONEY_KEYS = {"totalAmount"}
    rows = []
    items = detail.get("list") or []
    items = sort_and_assign_index(items, "purchase_return")
    extra = _pick_money(
        doc, _RETURN_MONEY_KEYS,
        "shortId", "totalAmount", "financeStatus",
        "statusName", "tagName",
    )
    # 采退单 purchaseOrderId 是数字 ID，转为字符串存储
    po_id = doc.get("purchaseOrderId")
    po_code = str(po_id) if po_id is not None else None
    for item in items:
        rows.append({
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

    return rows
