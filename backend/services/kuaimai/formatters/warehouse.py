"""
仓储 格式化器（Phase 5B 标签映射表模式）

格式化调拨单、入出库单、盘点单、下架单、货位库存等查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_item_with_labels, format_timestamp
from services.kuaimai.registry.base import ApiEntry

# ---------------------------------------------------------------------------
# 调拨单列表 — erp.allocate.task.query
# 修正: allocateNo→code / fromWarehouseName→outWarehouseName / toWarehouseName→inWarehouseName
# ---------------------------------------------------------------------------
_ALLOCATE_LABELS = {
    "code": "调拨单号", "shortId": "短号",
    "status": "状态",
    "outWarehouseName": "调出仓", "inWarehouseName": "调入仓",
    "outNum": "申请数量", "actualOutNum": "实际出库",
    "inNum": "实际入库", "diffNum": "差异数量",
    "outTotalAmount": "调拨金额", "inTotalAmount": "入库金额",
    "diffAmount": "差异金额",
    "creatorName": "创建人", "created": "创建时间",
    "labelName": "标签",
}
_ALLOCATE_TRANSFORMS: Dict[str, Callable] = {
    "created": format_timestamp,
    "outTotalAmount": lambda v: f"¥{v}" if v else "",
    "inTotalAmount": lambda v: f"¥{v}" if v else "",
    "diffAmount": lambda v: f"¥{v}" if v else "",
}

# ---------------------------------------------------------------------------
# 调拨单明细 — erp.allocate.task.detail.query
# 修正: title→itemOuterId / num→outNum
# ---------------------------------------------------------------------------
_ALLOCATE_DETAIL_LABELS = {
    "itemOuterId": "主编码", "outerId": "SKU编码",
    "outNum": "申请数量", "actualOutNum": "实际出库",
    "inNum": "实际入库",
    "price": "成本价",
    "diffNum": "差异数量", "diffAmount": "差异金额",
    "actualOutTotalAmount": "出库金额", "inTotalAmount": "入库金额",
    "refundNum": "拒收数量",
    "batchNo": "批次号", "productTime": "生产日期", "expireDate": "有效期",
    "remark": "备注",
}
_ALLOCATE_DETAIL_TRANSFORMS: Dict[str, Callable] = {
    "price": lambda v: f"¥{v}" if v else "",
    "diffAmount": lambda v: f"¥{v}" if v else "",
    "actualOutTotalAmount": lambda v: f"¥{v}" if v else "",
    "inTotalAmount": lambda v: f"¥{v}" if v else "",
}

# ---------------------------------------------------------------------------
# 入出库单 — other.in.order.query / other.out.order.query
# 修正: orderNo → code
# ---------------------------------------------------------------------------
_OTHER_IO_LABELS = {
    "code": "单号", "shortId": "短号",
    "customType": "出入库类型", "busyTypeDesc": "业务类型",
    "status": "状态", "statusName": "状态名",
    "warehouseName": "仓库",
    "supplierName": "供应商",
    "purchaseOrderCode": "关联采购单",
    "quantity": "总数量",
    "getGoodNum": "良品数", "getBadNum": "次品数",
    "shelvedQuantity": "已上架", "waitShelveQuantity": "待上架",
    "totalDetailFee": "总金额",
    "createrName": "创建人", "created": "创建时间",
    "remark": "备注",
}
_OTHER_IO_TRANSFORMS: Dict[str, Callable] = {
    "created": format_timestamp,
    "totalDetailFee": lambda v: f"¥{v / 100:.2f}" if v else "",
}

# ---------------------------------------------------------------------------
# 盘点单列表 — inventory.sheet.query
# 修正: sheetNo/orderNo → code
# ---------------------------------------------------------------------------
_INV_SHEET_LABELS = {
    "code": "盘点单号",
    "warehouseName": "仓库",
    "type": "类型", "status": "状态",
    "createdName": "创建人", "created": "创建时间",
    "submitName": "提交人", "submitted": "提交时间",
    "audditName": "审核人", "auddited": "审核时间",
    "remark": "备注",
}
_INV_SHEET_TRANSFORMS: Dict[str, Callable] = {
    "type": lambda v: {1: "正常盘点", 2: "即时盘点"}.get(v, str(v)),
    "status": lambda v: {1: "待提交", 2: "待审核", 3: "已审核",
                         4: "已作废"}.get(v, str(v)),
    "created": format_timestamp,
    "submitted": format_timestamp,
    "auddited": format_timestamp,
}

# ---------------------------------------------------------------------------
# 盘点单明细 — inventory.sheet.get
# 修正: sysQuantity→beforeNum / realQuantity→afterNum / diffQuantity→differentNum
# ---------------------------------------------------------------------------
_INV_SHEET_DETAIL_LABELS = {
    "sheetCode": "盘点单号",
    "title": "名称", "outerId": "编码",
    "propertiesName": "规格",
    "beforeNum": "系统数", "afterNum": "实盘数",
    "differentNum": "差异数", "differentAmount": "差异金额",
    "qualityType": "品质",
    "inventoryName": "盘点人", "inventoryTime": "盘点时间",
    "goodsSectionCode": "货位",
}
_INV_SHEET_DETAIL_TRANSFORMS: Dict[str, Callable] = {
    "qualityType": lambda v: "良品" if v == 1 else "次品",
    "differentAmount": lambda v: f"¥{v}" if v else "",
    "inventoryTime": format_timestamp,
}

# ---------------------------------------------------------------------------
# 下架单（字段名待API验证）
# ---------------------------------------------------------------------------
_UNSHELVE_LABELS = {
    "code": "单号", "orderNo": "单号",
    "status": "状态", "warehouseName": "仓库",
    "created": "时间",
}
_UNSHELVE_TRANSFORMS: Dict[str, Callable] = {"created": format_timestamp}

# ---------------------------------------------------------------------------
# 货位库存（字段名待API验证）
# ---------------------------------------------------------------------------
_GOODS_SECTION_LABELS = {
    "sectionName": "货位", "sectionCode": "货位编码",
    "title": "商品", "outerId": "编码",
    "quantity": "数量",
}

# ---------------------------------------------------------------------------
# 加工单（字段名待API验证）
# ---------------------------------------------------------------------------
_PROCESS_ORDER_LABELS = {
    "code": "单号", "orderNo": "单号",
    "status": "状态", "warehouseName": "仓库",
    "created": "时间",
}
_PROCESS_ORDER_TRANSFORMS: Dict[str, Callable] = {"created": format_timestamp}

# ---------------------------------------------------------------------------
# 批次效期库存（字段名待API验证）
# ---------------------------------------------------------------------------
_BATCH_STOCK_LABELS = {
    "title": "商品", "outerId": "编码",
    "batchNo": "批次", "quantity": "数量",
    "expireDate": "有效期", "productTime": "生产日期",
}

# ---------------------------------------------------------------------------
# 货位进出记录（字段名待API验证）
# ---------------------------------------------------------------------------
_SECTION_RECORD_LABELS = {
    "sectionName": "货位", "sectionCode": "货位编码",
    "title": "商品", "outerId": "编码",
    "bizType": "类型", "changeNum": "变动",
    "created": "时间",
}
_SECTION_RECORD_TRANSFORMS: Dict[str, Callable] = {"created": format_timestamp}


# ===== 公开 formatter 函数 =====

def format_allocate_list(data: Any, entry: ApiEntry) -> str:
    """调拨单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到调拨单"
    lines = [f"共 {total} 条调拨单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _ALLOCATE_LABELS, transforms=_ALLOCATE_TRANSFORMS))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_allocate_detail(data: Any, entry: ApiEntry) -> str:
    """调拨单明细"""
    # 头部信息
    header = format_item_with_labels(
        data, _ALLOCATE_LABELS, transforms=_ALLOCATE_TRANSFORMS)
    lines = [f"调拨单详情: {header}"]

    items = data.get("items") or data.get("details") or data.get("list") or []
    if items:
        lines.append(f"\n调拨商品（共{len(items)}个）：")
        for it in items[:20]:
            lines.append("  - " + format_item_with_labels(
                it, _ALLOCATE_DETAIL_LABELS,
                transforms=_ALLOCATE_DETAIL_TRANSFORMS))
    return "\n".join(lines)


def format_other_in_out_list(data: Any, entry: ApiEntry) -> str:
    """其他入库/出库单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到入出库单"
    lines = [f"共 {total} 条记录：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _OTHER_IO_LABELS, transforms=_OTHER_IO_TRANSFORMS))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_inventory_sheet_list(data: Any, entry: ApiEntry) -> str:
    """盘点单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到盘点单"
    lines = [f"共 {total} 条盘点单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _INV_SHEET_LABELS, transforms=_INV_SHEET_TRANSFORMS))
    return "\n".join(lines)


def format_inventory_sheet_detail(data: Any, entry: ApiEntry) -> str:
    """盘点单明细"""
    header = format_item_with_labels(
        data, _INV_SHEET_LABELS, transforms=_INV_SHEET_TRANSFORMS)
    lines = [f"盘点单详情: {header}"]

    items = data.get("items") or data.get("details") or data.get("list") or []
    if items:
        lines.append(f"\n盘点商品（共{len(items)}个）：")
        for it in items[:20]:
            lines.append("  - " + format_item_with_labels(
                it, _INV_SHEET_DETAIL_LABELS,
                transforms=_INV_SHEET_DETAIL_TRANSFORMS))
    return "\n".join(lines)


def format_unshelve_list(data: Any, entry: ApiEntry) -> str:
    """下架单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到下架单"
    lines = [f"共 {total} 条下架单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _UNSHELVE_LABELS, transforms=_UNSHELVE_TRANSFORMS))
    return "\n".join(lines)


def format_goods_section_list(data: Any, entry: ApiEntry) -> str:
    """货位库存列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到货位库存信息"
    lines = [f"共 {total} 条货位库存：\n"]
    for item in items[:30]:
        lines.append("- " + format_item_with_labels(
            item, _GOODS_SECTION_LABELS))
    return "\n".join(lines)


def format_process_order_list(data: Any, entry: ApiEntry) -> str:
    """加工单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到加工单"
    lines = [f"共 {total} 条加工单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _PROCESS_ORDER_LABELS,
            transforms=_PROCESS_ORDER_TRANSFORMS))
    return "\n".join(lines)


def format_batch_stock_list(data: Any, entry: ApiEntry) -> str:
    """批次效期库存"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到批次库存信息"
    lines = [f"共 {total} 条批次库存：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _BATCH_STOCK_LABELS))
    return "\n".join(lines)


def format_section_record_list(data: Any, entry: ApiEntry) -> str:
    """货位进出记录"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到货位进出记录"
    lines = [f"共 {total} 条货位进出记录：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _SECTION_RECORD_LABELS,
            transforms=_SECTION_RECORD_TRANSFORMS))
    return "\n".join(lines)


WAREHOUSE_FORMATTERS: Dict[str, Callable] = {
    "format_allocate_list": format_allocate_list,
    "format_allocate_detail": format_allocate_detail,
    "format_other_in_out_list": format_other_in_out_list,
    "format_inventory_sheet_list": format_inventory_sheet_list,
    "format_inventory_sheet_detail": format_inventory_sheet_detail,
    "format_unshelve_list": format_unshelve_list,
    "format_goods_section_list": format_goods_section_list,
    "format_process_order_list": format_process_order_list,
    "format_batch_stock_list": format_batch_stock_list,
    "format_section_record_list": format_section_record_list,
}

# 返回字段注册表（供 erp_api_search 生成文档）
WAREHOUSE_RESPONSE_FIELDS: Dict[str, Dict] = {
    "format_allocate_list": {"main": _ALLOCATE_LABELS},
    "format_allocate_detail": {
        "main": _ALLOCATE_LABELS,
        "items": _ALLOCATE_DETAIL_LABELS,
        "items_key": "items",
    },
    "format_other_in_out_list": {"main": _OTHER_IO_LABELS},
    "format_inventory_sheet_list": {"main": _INV_SHEET_LABELS},
    "format_inventory_sheet_detail": {
        "main": _INV_SHEET_LABELS,
        "items": _INV_SHEET_DETAIL_LABELS,
        "items_key": "items",
    },
    "format_unshelve_list": {"main": _UNSHELVE_LABELS},
    "format_goods_section_list": {"main": _GOODS_SECTION_LABELS},
    "format_process_order_list": {"main": _PROCESS_ORDER_LABELS},
    "format_batch_stock_list": {"main": _BATCH_STOCK_LABELS},
    "format_section_record_list": {"main": _SECTION_RECORD_LABELS},
}
