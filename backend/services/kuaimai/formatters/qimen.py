"""
奇门接口格式化器

格式化淘宝奇门接口（kuaimai.order.list.query / kuaimai.refund.list.query）
的响应数据为 Agent 大脑可读的文本。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_timestamp
from services.kuaimai.registry.base import ApiEntry

# 订单类型映射
_ORDER_TYPE_MAP = {
    "0": "普通", "1": "货到付款", "3": "平台", "4": "线下",
    "6": "预售", "7": "合并", "8": "拆分", "9": "加急",
    "10": "空包", "12": "门店", "13": "换货", "14": "补发",
    "33": "分销", "34": "供销", "50": "店铺预售", "99": "出库单",
}

# 售后类型映射
_REFUND_TYPE_MAP = {
    1: "退款", 2: "退货", 3: "补发", 4: "换货", 5: "发货前退款",
}

# 售后工单状态映射
_REFUND_STATUS_MAP = {
    1: "未分配", 2: "未解决", 3: "优先退款", 4: "同意",
    5: "拒绝", 6: "确认退货", 7: "确认发货", 8: "确认退款",
    9: "处理完成", 10: "作废",
}


def format_qimen_order_list(data: Any, entry: ApiEntry) -> str:
    """淘宝订单列表（trades key）"""
    items = data.get("trades") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的淘宝订单"
    lines = [f"共找到 {total} 条淘宝订单：\n"]
    for order in items[:20]:
        lines.append(_format_taobao_order(order))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_qimen_refund_list(data: Any, entry: ApiEntry) -> str:
    """淘宝售后单列表（workOrders key）"""
    items = data.get("workOrders") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的淘宝售后单"
    lines = [f"共找到 {total} 条售后单：\n"]
    for wo in items[:20]:
        lines.append(_format_taobao_refund(wo))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def _format_taobao_order(order: Dict[str, Any]) -> str:
    """格式化单个淘宝订单"""
    tid = order.get("tid") or ""
    sid = order.get("sid") or ""
    sys_status = order.get("sysStatus") or ""
    ch_status = order.get("chSysStatus") or sys_status
    buyer = order.get("buyerNick") or "（隐私保护）"
    payment = order.get("payment") or "0"
    shop = order.get("shopName") or ""
    source = order.get("source") or ""
    warehouse = order.get("warehouseName") or ""
    created = format_timestamp(order.get("created"))
    pay_time = format_timestamp(order.get("payTime"))
    order_type = _ORDER_TYPE_MAP.get(str(order.get("type", "")), "")

    line1 = f"- 订单: {tid} | 系统单号: {sid}"
    parts2 = [f"状态: {ch_status}", f"买家: {buyer}", f"店铺: {shop}"]
    if order_type:
        parts2.append(f"类型: {order_type}")
    if source:
        parts2.append(f"来源: {source}")
    if warehouse:
        parts2.append(f"仓库: {warehouse}")
    line2 = "  " + " | ".join(parts2)
    line3 = f"  金额: ¥{payment} | 创建: {created} | 付款: {pay_time}"

    # 商品明细（最多3条）
    sub_lines = []
    for sub in (order.get("orders") or [])[:3]:
        title = sub.get("sysTitle") or sub.get("title") or ""
        num = sub.get("num", 0)
        outer_id = sub.get("sysOuterId") or sub.get("outerId") or ""
        parts = [f"    · {title} x{num}"]
        if outer_id:
            parts.append(f"编码: {outer_id}")
        sub_lines.append(" | ".join(parts))

    result = f"{line1}\n{line2}\n{line3}"
    if sub_lines:
        result += "\n" + "\n".join(sub_lines)
    return result


def _format_taobao_refund(wo: Dict[str, Any]) -> str:
    """格式化单个淘宝售后工单"""
    wo_id = wo.get("id") or ""
    tid = wo.get("tid") or ""
    sid = wo.get("sid") or ""
    shop = wo.get("shopName") or ""
    as_type = _REFUND_TYPE_MAP.get(wo.get("afterSaleType"), "")
    status = _REFUND_STATUS_MAP.get(wo.get("status"), str(wo.get("status", "")))
    refund_money = wo.get("refundMoney") or 0
    reason_code = wo.get("reason")
    text_reason = wo.get("textReason") or ""
    remark = wo.get("remark") or ""
    created = format_timestamp(wo.get("created"))

    line1 = f"- 工单: {wo_id} | 订单: {tid} | 系统单号: {sid}"
    parts2 = []
    if as_type:
        parts2.append(f"类型: {as_type}")
    parts2.append(f"状态: {status}")
    if shop:
        parts2.append(f"店铺: {shop}")
    parts2.append(f"退款: ¥{refund_money}")
    line2 = "  " + " | ".join(parts2)

    result = f"{line1}\n{line2}"
    if text_reason:
        result += f"\n  原因: {text_reason}"
    if remark:
        result += f"\n  备注: {remark}"
    if created:
        result += f"\n  创建: {created}"

    # 售后商品明细（最多3条）
    for item in (wo.get("items") or [])[:3]:
        title = item.get("title") or ""
        count = item.get("receivableCount") or 0
        outer_id = item.get("outerId") or ""
        parts = [f"    · {title} x{count}"]
        if outer_id:
            parts.append(f"编码: {outer_id}")
        result += "\n" + " | ".join(parts)

    return result


QIMEN_FORMATTERS: Dict[str, Callable] = {
    "format_qimen_order_list": format_qimen_order_list,
    "format_qimen_refund_list": format_qimen_refund_list,
}
