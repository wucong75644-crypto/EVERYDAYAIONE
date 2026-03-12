"""
仓储 格式化器

格式化调拨单、入出库单、盘点单、下架单、货位库存等查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_timestamp
from services.kuaimai.registry.base import ApiEntry


def format_allocate_list(data: Any, entry: ApiEntry) -> str:
    """调拨单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到调拨单"
    lines = [f"共 {total} 条调拨单：\n"]
    for item in items[:20]:
        order_no = item.get("allocateNo") or item.get("orderNo") or ""
        status = item.get("status") or ""
        from_wh = item.get("fromWarehouseName") or ""
        to_wh = item.get("toWarehouseName") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 调拨单号: {order_no}"]
        if from_wh and to_wh:
            parts.append(f"{from_wh} → {to_wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_allocate_detail(data: Any, entry: ApiEntry) -> str:
    """调拨单明细"""
    order_no = data.get("allocateNo") or data.get("orderNo") or ""
    status = data.get("status") or ""
    from_wh = data.get("fromWarehouseName") or ""
    to_wh = data.get("toWarehouseName") or ""
    lines = [f"调拨单详情: {order_no}"]
    if from_wh and to_wh:
        lines.append(f"  调出仓: {from_wh} → 调入仓: {to_wh}")
    if status:
        lines.append(f"  状态: {status}")
    items = data.get("items") or data.get("details") or data.get("list") or []
    if items:
        lines.append(f"\n  调拨商品（共{len(items)}个）：")
        for it in items[:20]:
            title = it.get("title") or it.get("itemTitle") or ""
            code = it.get("outerId") or ""
            num = it.get("num") or it.get("quantity") or 0
            lines.append(f"    - {title} | 编码: {code} | 数量: {num}")
    return "\n".join(lines)


def format_other_in_out_list(data: Any, entry: ApiEntry) -> str:
    """其他入库/出库单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到入出库单"
    lines = [f"共 {total} 条记录：\n"]
    for item in items[:20]:
        order_no = item.get("orderNo") or ""
        status = item.get("status") or ""
        wh = item.get("warehouseName") or ""
        time = format_timestamp(item.get("created"))
        remark = item.get("remark") or ""
        parts = [f"- 单号: {order_no}"]
        if wh:
            parts.append(f"仓库: {wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        if remark:
            parts.append(f"备注: {remark[:30]}")
        lines.append(" | ".join(parts))
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
        sheet_no = item.get("sheetNo") or item.get("orderNo") or ""
        status = item.get("status") or ""
        wh = item.get("warehouseName") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 盘点单号: {sheet_no}"]
        if wh:
            parts.append(f"仓库: {wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_inventory_sheet_detail(data: Any, entry: ApiEntry) -> str:
    """盘点单明细"""
    sheet_no = data.get("sheetNo") or data.get("orderNo") or ""
    status = data.get("status") or ""
    wh = data.get("warehouseName") or ""
    lines = [f"盘点单详情: {sheet_no}"]
    if wh:
        lines.append(f"  仓库: {wh}")
    if status:
        lines.append(f"  状态: {status}")
    items = data.get("items") or data.get("details") or data.get("list") or []
    if items:
        lines.append(f"\n  盘点商品（共{len(items)}个）：")
        for it in items[:20]:
            title = it.get("title") or it.get("itemTitle") or ""
            code = it.get("outerId") or ""
            sys_qty = it.get("sysQuantity") or it.get("systemNum") or 0
            real_qty = it.get("realQuantity") or it.get("realNum") or 0
            diff = it.get("diffQuantity") or it.get("diffNum") or ""
            line = f"    - {title} | 编码: {code} | 系统: {sys_qty} | 实际: {real_qty}"
            if diff:
                line += f" | 差异: {diff}"
            lines.append(line)
    return "\n".join(lines)


def format_unshelve_list(data: Any, entry: ApiEntry) -> str:
    """下架单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到下架单"
    lines = [f"共 {total} 条下架单：\n"]
    for item in items[:20]:
        order_no = item.get("orderNo") or ""
        status = item.get("status") or ""
        wh = item.get("warehouseName") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 下架单号: {order_no}"]
        if wh:
            parts.append(f"仓库: {wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_goods_section_list(data: Any, entry: ApiEntry) -> str:
    """货位库存列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到货位库存信息"
    lines = [f"共 {total} 条货位库存：\n"]
    for item in items[:30]:
        section = item.get("sectionName") or item.get("sectionCode") or ""
        title = item.get("title") or item.get("itemTitle") or ""
        code = item.get("outerId") or ""
        qty = item.get("quantity") or item.get("num") or 0
        parts = [f"- 货位: {section}"]
        if title:
            parts.append(f"商品: {title}")
        if code:
            parts.append(f"编码: {code}")
        parts.append(f"数量: {qty}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_process_order_list(data: Any, entry: ApiEntry) -> str:
    """加工单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到加工单"
    lines = [f"共 {total} 条加工单：\n"]
    for item in items[:20]:
        order_no = item.get("orderNo") or ""
        status = item.get("status") or ""
        wh = item.get("warehouseName") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 加工单号: {order_no}"]
        if wh:
            parts.append(f"仓库: {wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_batch_stock_list(data: Any, entry: ApiEntry) -> str:
    """批次效期库存"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到批次库存信息"
    lines = [f"共 {total} 条批次库存：\n"]
    for item in items[:20]:
        title = item.get("title") or item.get("itemTitle") or ""
        batch = item.get("batchNo") or ""
        qty = item.get("quantity") or item.get("num") or 0
        expire = item.get("expireDate") or ""
        parts = [f"- {title}"]
        if batch:
            parts.append(f"批次: {batch}")
        parts.append(f"数量: {qty}")
        if expire:
            parts.append(f"有效期: {expire}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_section_record_list(data: Any, entry: ApiEntry) -> str:
    """货位进出记录"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到货位进出记录"
    lines = [f"共 {total} 条货位进出记录：\n"]
    for item in items[:20]:
        section = item.get("sectionName") or item.get("sectionCode") or ""
        title = item.get("title") or item.get("itemTitle") or ""
        biz_type = item.get("bizType") or ""
        qty = item.get("changeNum") or item.get("num") or 0
        time = format_timestamp(item.get("created"))
        parts = [f"- [{time}] 货位: {section}"]
        if title:
            parts.append(f"商品: {title}")
        if biz_type:
            parts.append(f"类型: {biz_type}")
        parts.append(f"变动: {qty}")
        lines.append(" | ".join(parts))
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
