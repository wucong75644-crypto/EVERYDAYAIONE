"""
售后 格式化器

格式化售后工单、退货、维修单、补款、日志等查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_timestamp
from services.kuaimai.registry.base import ApiEntry


def format_aftersale_list(data: Any, entry: ApiEntry) -> str:
    """售后工单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到售后工单"
    lines = [f"共 {total} 条售后工单：\n"]
    for item in items[:20]:
        lines.append(_format_aftersale_item(item))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_refund_warehouse(data: Any, entry: ApiEntry) -> str:
    """销退入库单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到销退入库单"
    lines = [f"共 {total} 条销退入库单：\n"]
    for item in items[:20]:
        order_no = item.get("orderNo") or item.get("refundNo") or ""
        tid = item.get("tid") or ""
        wh = item.get("warehouseName") or ""
        status = item.get("status") or ""
        time = format_timestamp(item.get("created") or item.get("modified"))
        parts = [f"- 单号: {order_no}"]
        if tid:
            parts.append(f"订单: {tid}")
        if wh:
            parts.append(f"仓库: {wh}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_replenish_list(data: Any, entry: ApiEntry) -> str:
    """登记补款列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到补款记录"
    lines = [f"共 {total} 条补款记录：\n"]
    for item in items[:20]:
        tid = item.get("tid") or ""
        amount = item.get("amount") or item.get("money") or 0
        status = item.get("status") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 订单: {tid}"]
        parts.append(f"金额: ¥{amount}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_repair_list(data: Any, entry: ApiEntry) -> str:
    """维修单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到维修单"
    lines = [f"共 {total} 条维修单：\n"]
    for item in items[:20]:
        order_no = item.get("repairNo") or item.get("orderNo") or ""
        status = item.get("status") or ""
        customer = item.get("customerName") or ""
        time = format_timestamp(item.get("created"))
        parts = [f"- 维修单号: {order_no}"]
        if customer:
            parts.append(f"客户: {customer}")
        if status:
            parts.append(f"状态: {status}")
        if time:
            parts.append(f"时间: {time}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_repair_detail(data: Any, entry: ApiEntry) -> str:
    """维修单详情"""
    order_no = data.get("repairNo") or data.get("orderNo") or ""
    status = data.get("status") or ""
    customer = data.get("customerName") or ""
    remark = data.get("remark") or ""
    lines = [f"维修单详情: {order_no}"]
    if customer:
        lines.append(f"  客户: {customer}")
    if status:
        lines.append(f"  状态: {status}")
    if remark:
        lines.append(f"  备注: {remark}")
    items = data.get("items") or data.get("details") or []
    if items:
        lines.append(f"\n  维修商品（共{len(items)}个）：")
        for it in items[:10]:
            title = it.get("title") or it.get("itemTitle") or ""
            num = it.get("num") or 1
            lines.append(f"    - {title} x{num}")
    return "\n".join(lines)


def format_aftersale_log(data: Any, entry: ApiEntry) -> str:
    """售后操作日志"""
    items = data.get("list") or []
    if not items:
        return "未找到售后操作日志"
    lines = [f"共 {len(items)} 条操作日志：\n"]
    for log in items[:30]:
        time = format_timestamp(log.get("operTime") or log.get("created"))
        action = log.get("action") or log.get("operateType") or ""
        oper = log.get("operName") or log.get("operator") or ""
        remark = log.get("remark") or ""
        line = f"- [{time}] {action}"
        if oper:
            line += f" | 操作人: {oper}"
        if remark:
            line += f" | {remark}"
        lines.append(line)
    return "\n".join(lines)


def _format_aftersale_item(item: Dict[str, Any]) -> str:
    """格式化单个售后工单"""
    tid = item.get("tid") or ""
    refund_id = item.get("refundId") or item.get("workOrderNo") or ""
    as_type = item.get("afterSaleType") or item.get("type") or ""
    status = item.get("status") or ""
    reason = item.get("reason") or ""
    buyer = item.get("buyerNick") or ""
    amount = item.get("refundFee") or item.get("amount") or ""
    time = format_timestamp(item.get("created"))

    line1 = f"- 工单: {refund_id} | 订单: {tid}"
    parts2 = []
    if as_type:
        parts2.append(f"类型: {as_type}")
    if status:
        parts2.append(f"状态: {status}")
    if buyer:
        parts2.append(f"买家: {buyer}")
    if amount:
        parts2.append(f"退款: ¥{amount}")
    line2 = "  " + " | ".join(parts2) if parts2 else ""
    result = line1
    if line2:
        result += f"\n{line2}"
    if reason:
        result += f"\n  原因: {reason}"
    if time:
        result += f"\n  创建: {time}"
    return result


AFTERSALES_FORMATTERS: Dict[str, Callable] = {
    "format_aftersale_list": format_aftersale_list,
    "format_refund_warehouse": format_refund_warehouse,
    "format_replenish_list": format_replenish_list,
    "format_repair_list": format_repair_list,
    "format_repair_detail": format_repair_detail,
    "format_aftersale_log": format_aftersale_log,
}
