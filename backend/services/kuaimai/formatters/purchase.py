"""
采购 格式化器（Phase 5B 标签映射表模式）

格式化供应商、采购单、收货单、采退单、上架单等查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_item_with_labels, format_timestamp
from services.kuaimai.registry.base import ApiEntry

# ---------------------------------------------------------------------------
# 供应商列表 — supplier.list.query
# 修正: contact → contactName
# ---------------------------------------------------------------------------
_SUPPLIER_LABELS = {
    "name": "名称", "code": "编码",
    "status": "状态",
    "contactName": "联系人",
    "mobile": "手机", "phone": "电话",
    "email": "邮箱",
    "categoryName": "供应商分类",
    "billType": "结算方式",
    "planReceiveDay": "预计交期(天)",
    "address": "地址",
    "remark": "备注",
}
_SUPPLIER_TRANSFORMS: Dict[str, Callable] = {
    "status": lambda v: {0: "停用", 1: "正常"}.get(v, str(v)),
}

# ---------------------------------------------------------------------------
# 采购单列表 — purchase.order.query
# 修正: purchaseNo/orderNo → code
# ---------------------------------------------------------------------------
_PURCHASE_ORDER_LABELS = {
    "code": "采购单号", "shortId": "短号",
    "supplierName": "供应商",
    "status": "状态",
    "totalAmount": "总金额", "actualTotalAmount": "实际金额",
    "quantity": "总数量",
    "arrivedQuantity": "已到货", "receiveQuantity": "已收货",
    "receiveWarehouseName": "收货仓库",
    "deliveryDate": "交货日期",
    "createrName": "创建人", "created": "创建时间",
    "remark": "备注",
    "financeStatus": "财务状态",
}
_PURCHASE_ORDER_TRANSFORMS: Dict[str, Callable] = {
    "totalAmount": lambda v: f"¥{v}" if v else "",
    "actualTotalAmount": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
    "deliveryDate": format_timestamp,
}

# ---------------------------------------------------------------------------
# 采购单明细 — purchase.order.get
# 修正: num/quantity → count / title → itemOuterId
# ---------------------------------------------------------------------------
_PURCHASE_DETAIL_LABELS = {
    "itemOuterId": "主编码", "outerId": "SKU编码",
    "count": "数量",
    "price": "单价",
    "amount": "金额(调前)", "totalFee": "金额(调后)",
    "amendAmount": "调整金额",
    "deliveryDate": "交货日期",
    "remark": "备注",
}
_PURCHASE_DETAIL_TRANSFORMS: Dict[str, Callable] = {
    "price": lambda v: f"¥{v}" if v else "",
    "amount": lambda v: f"¥{v}" if v else "",
    "totalFee": lambda v: f"¥{v}" if v else "",
    "amendAmount": lambda v: f"¥{v}" if v else "",
    "deliveryDate": format_timestamp,
}

# ---------------------------------------------------------------------------
# 采退单列表 — purchase.return.list.query
# 修正: returnNo/orderNo → code / created → gmCreate
# ---------------------------------------------------------------------------
_PURCHASE_RETURN_LABELS = {
    "code": "采退单号",
    "supplierName": "供应商",
    "status": "状态", "statusName": "状态名",
    "totalAmount": "总金额",
    "totalCount": "总数量",
    "returnNum": "退货数量", "actualReturnNum": "实退数量",
    "warehouseName": "仓库",
    "createrName": "创建人",
    "gmCreate": "创建时间",
    "financeStatus": "财务状态",
}
_PURCHASE_RETURN_TRANSFORMS: Dict[str, Callable] = {
    "totalAmount": lambda v: f"¥{v}" if v else "",
    "gmCreate": format_timestamp,
}

# ---------------------------------------------------------------------------
# 收货单列表 — other.in.order.query (采购入库)
# 修正: entryNo/orderNo → code
# ---------------------------------------------------------------------------
_WH_ENTRY_LABELS = {
    "code": "收货单号",
    "purchaseOrderCode": "关联采购单",
    "supplierName": "供应商",
    "warehouseName": "仓库",
    "status": "状态",
    "quantity": "总数量",
    "receiveQuantity": "已收货",
    "shelvedQuantity": "已上架",
    "getGoodNum": "良品数", "getBadNum": "次品数",
    "totalDetailFee": "总金额",
    "createrName": "创建人", "created": "创建时间",
    "busyTypeDesc": "业务类型",
}
_WH_ENTRY_TRANSFORMS: Dict[str, Callable] = {
    "created": format_timestamp,
    "totalDetailFee": lambda v: f"¥{v / 100:.2f}" if v else "",
}

# ---------------------------------------------------------------------------
# 采购建议 — sale.purchase.strategy.query
# 修正: suggestNum→purchaseStock / availableStock→stockoutNum / title→itemOuterId
# ---------------------------------------------------------------------------
_STRATEGY_LABELS = {
    "itemOuterId": "主编码", "outerId": "SKU编码",
    "propertiesName": "规格",
    "purchaseStock": "建议采购数",
    "stockoutNum": "缺货数",
    "itemCatName": "分类",
}

# ---------------------------------------------------------------------------
# 上架单（字段名待API验证）
# ---------------------------------------------------------------------------
_SHELF_LABELS = {
    "code": "单号", "shelfNo": "单号",
    "status": "状态", "warehouseName": "仓库",
    "created": "时间",
}
_SHELF_TRANSFORMS: Dict[str, Callable] = {"created": format_timestamp}


# ===== 公开 formatter 函数 =====

def format_supplier_list(data: Any, entry: ApiEntry) -> str:
    """供应商列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到供应商"
    lines = [f"共 {total} 个供应商：\n"]
    for s in items[:30]:
        lines.append("- " + format_item_with_labels(
            s, _SUPPLIER_LABELS, transforms=_SUPPLIER_TRANSFORMS))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}个，共{total}个）")
    return "\n".join(lines)


def format_purchase_order_list(data: Any, entry: ApiEntry) -> str:
    """采购单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到采购单"
    lines = [f"共 {total} 条采购单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _PURCHASE_ORDER_LABELS,
            transforms=_PURCHASE_ORDER_TRANSFORMS))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_purchase_order_detail(data: Any, entry: ApiEntry) -> str:
    """采购单详情"""
    header = format_item_with_labels(
        data, _PURCHASE_ORDER_LABELS,
        transforms=_PURCHASE_ORDER_TRANSFORMS)
    lines = [f"采购单详情: {header}"]

    items = data.get("items") or data.get("details") or data.get("list") or []
    if items:
        lines.append(f"\n采购商品（共{len(items)}个）：")
        for it in items[:20]:
            lines.append("  - " + format_item_with_labels(
                it, _PURCHASE_DETAIL_LABELS,
                transforms=_PURCHASE_DETAIL_TRANSFORMS))
    return "\n".join(lines)


def format_purchase_return_list(data: Any, entry: ApiEntry) -> str:
    """采退单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到采退单"
    lines = [f"共 {total} 条采退单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _PURCHASE_RETURN_LABELS,
            transforms=_PURCHASE_RETURN_TRANSFORMS))
    return "\n".join(lines)


def format_warehouse_entry_list(data: Any, entry: ApiEntry) -> str:
    """收货单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到收货单"
    lines = [f"共 {total} 条收货单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _WH_ENTRY_LABELS, transforms=_WH_ENTRY_TRANSFORMS))
    return "\n".join(lines)


def format_shelf_list(data: Any, entry: ApiEntry) -> str:
    """上架单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到上架单"
    lines = [f"共 {total} 条上架单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _SHELF_LABELS, transforms=_SHELF_TRANSFORMS))
    return "\n".join(lines)


def format_purchase_strategy(data: Any, entry: ApiEntry) -> str:
    """采购建议"""
    items = (data.get("purchaseStrategyList")
             or data.get("list") or [])
    total = data.get("total", len(items))
    if not items:
        return "暂无采购建议"
    lines = [f"共 {total} 条采购建议：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _STRATEGY_LABELS))
    return "\n".join(lines)


PURCHASE_FORMATTERS: Dict[str, Callable] = {
    "format_supplier_list": format_supplier_list,
    "format_purchase_order_list": format_purchase_order_list,
    "format_purchase_order_detail": format_purchase_order_detail,
    "format_purchase_return_list": format_purchase_return_list,
    "format_warehouse_entry_list": format_warehouse_entry_list,
    "format_shelf_list": format_shelf_list,
    "format_purchase_strategy": format_purchase_strategy,
}

# 返回字段注册表（供 erp_api_search 生成文档）
PURCHASE_RESPONSE_FIELDS: Dict[str, Dict] = {
    "format_supplier_list": {"main": _SUPPLIER_LABELS},
    "format_purchase_order_list": {"main": _PURCHASE_ORDER_LABELS},
    "format_purchase_order_detail": {
        "main": _PURCHASE_ORDER_LABELS,
        "items": _PURCHASE_DETAIL_LABELS,
        "items_key": "items",
    },
    "format_purchase_return_list": {"main": _PURCHASE_RETURN_LABELS},
    "format_warehouse_entry_list": {"main": _WH_ENTRY_LABELS},
    "format_shelf_list": {"main": _SHELF_LABELS},
    "format_purchase_strategy": {"main": _STRATEGY_LABELS},
}
