"""
采购 格式化器

格式化供应商、采购单、收货单、采退单、上架单等查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_timestamp
from services.kuaimai.registry.base import ApiEntry


def format_supplier_list(data: Any, entry: ApiEntry) -> str:
    """供应商列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到供应商"
    lines = [f"共 {total} 个供应商：\n"]
    for s in items[:30]:
        name = s.get("supplierName") or s.get("name") or ""
        code = s.get("supplierCode") or s.get("code") or ""
        contact = s.get("contact") or ""
        phone = s.get("phone") or s.get("mobile") or ""
        status = s.get("status") or ""
        parts = [f"- {name}"]
        if code:
            parts.append(f"编码: {code}")
        if contact:
            parts.append(f"联系人: {contact}")
        if phone:
            parts.append(f"电话: {phone}")
        if status:
            parts.append(f"状态: {status}")
        lines.append(" | ".join(parts))
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
        order_no = item.get("purchaseNo") or item.get("orderNo") or ""
        supplier = item.get("supplierName") or ""
        status = item.get("status") or ""
        amount = item.get("totalAmount") or item.get("amount") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 采购单号: {order_no}"]
        if supplier:
            parts.append(f"供应商: {supplier}")
        if status:
            parts.append(f"状态: {status}")
        if amount:
            parts.append(f"金额: ¥{amount}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_purchase_order_detail(data: Any, entry: ApiEntry) -> str:
    """采购单详情"""
    order_no = data.get("purchaseNo") or data.get("orderNo") or ""
    supplier = data.get("supplierName") or ""
    status = data.get("status") or ""
    amount = data.get("totalAmount") or data.get("amount") or ""
    lines = [f"采购单详情: {order_no}"]
    if supplier:
        lines.append(f"  供应商: {supplier}")
    if status:
        lines.append(f"  状态: {status}")
    if amount:
        lines.append(f"  总金额: ¥{amount}")
    items = data.get("items") or data.get("details") or data.get("list") or []
    if items:
        lines.append(f"\n  采购商品（共{len(items)}个）：")
        for it in items[:20]:
            title = it.get("title") or it.get("itemTitle") or ""
            code = it.get("outerId") or ""
            num = it.get("num") or it.get("quantity") or 0
            price = it.get("price") or it.get("purchasePrice") or ""
            line = f"    - {title} | 编码: {code} | 数量: {num}"
            if price:
                line += f" | 单价: ¥{price}"
            lines.append(line)
    return "\n".join(lines)


def format_purchase_return_list(data: Any, entry: ApiEntry) -> str:
    """采退单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到采退单"
    lines = [f"共 {total} 条采退单：\n"]
    for item in items[:20]:
        order_no = item.get("returnNo") or item.get("orderNo") or ""
        supplier = item.get("supplierName") or ""
        status = item.get("status") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 采退单号: {order_no}"]
        if supplier:
            parts.append(f"供应商: {supplier}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_warehouse_entry_list(data: Any, entry: ApiEntry) -> str:
    """收货单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到收货单"
    lines = [f"共 {total} 条收货单：\n"]
    for item in items[:20]:
        order_no = item.get("entryNo") or item.get("orderNo") or ""
        supplier = item.get("supplierName") or ""
        wh = item.get("warehouseName") or ""
        status = item.get("status") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 收货单号: {order_no}"]
        if supplier:
            parts.append(f"供应商: {supplier}")
        if wh:
            parts.append(f"仓库: {wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_shelf_list(data: Any, entry: ApiEntry) -> str:
    """上架单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到上架单"
    lines = [f"共 {total} 条上架单：\n"]
    for item in items[:20]:
        order_no = item.get("shelfNo") or item.get("orderNo") or ""
        status = item.get("status") or ""
        wh = item.get("warehouseName") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 上架单号: {order_no}"]
        if wh:
            parts.append(f"仓库: {wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_purchase_strategy(data: Any, entry: ApiEntry) -> str:
    """采购建议"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "暂无采购建议"
    lines = [f"共 {total} 条采购建议：\n"]
    for item in items[:20]:
        title = item.get("title") or item.get("itemTitle") or ""
        code = item.get("outerId") or ""
        suggest_num = item.get("suggestNum") or item.get("purchaseNum") or 0
        stock = item.get("availableStock") or item.get("stock") or 0
        parts = [f"- {title}"]
        if code:
            parts.append(f"编码: {code}")
        parts.append(f"库存: {stock}")
        parts.append(f"建议采购: {suggest_num}")
        lines.append(" | ".join(parts))
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
