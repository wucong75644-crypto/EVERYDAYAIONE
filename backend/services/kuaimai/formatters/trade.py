"""
交易/物流 格式化器

从 service.py 迁移: _format_order, _format_shipment
新增: 操作日志、快递单号、波次等格式化
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_timestamp
from services.kuaimai.registry.base import ApiEntry


def format_order_list(data: Any, entry: ApiEntry) -> str:
    """订单列表"""
    items = data.get("list") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的订单"
    lines = [f"共找到 {total} 条订单：\n"]
    for order in items[:20]:
        lines.append(_format_order(order))
    if total > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_shipment_list(data: Any, entry: ApiEntry) -> str:
    """出库/物流列表"""
    items = data.get("list") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的出库/物流记录"
    lines = [f"共找到 {total} 条出库记录：\n"]
    for item in items[:20]:
        lines.append(_format_shipment(item))
    if total > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_outstock_order_list(data: Any, entry: ApiEntry) -> str:
    """销售出库单列表"""
    items = data.get("list") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的出库单"
    lines = [f"共找到 {total} 条出库单：\n"]
    for item in items[:20]:
        lines.append(_format_shipment(item))
    if total > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_order_log(data: Any, entry: ApiEntry) -> str:
    """订单操作日志"""
    items = data.get("list") or []
    if not items:
        return "未找到订单操作日志"
    lines = [f"共 {len(items)} 条操作日志：\n"]
    for log in items[:30]:
        time = format_timestamp(log.get("operTime") or log.get("created"))
        action = log.get("action") or log.get("operate") or ""
        oper = log.get("operName") or log.get("operator") or ""
        remark = log.get("remark") or ""
        line = f"- [{time}] {action}"
        if oper:
            line += f" | 操作人: {oper}"
        if remark:
            line += f" | {remark}"
        lines.append(line)
    return "\n".join(lines)


def format_express_list(data: Any, entry: ApiEntry) -> str:
    """多快递单号查询"""
    items = data.get("list") or []
    if not items:
        return "未找到快递信息"
    lines = [f"共 {len(items)} 条快递记录：\n"]
    for item in items[:20]:
        tid = item.get("tid") or ""
        out_sid = item.get("outSid") or ""
        company = item.get("expressCompanyName") or ""
        lines.append(f"- 订单: {tid} | 快递: {company} | 单号: {out_sid}")
    return "\n".join(lines)


def format_logistics_company(data: Any, entry: ApiEntry) -> str:
    """物流公司列表"""
    items = data.get("list") or []
    if not items:
        return "暂无物流公司信息"
    lines = [f"共 {len(items)} 家物流公司：\n"]
    for c in items[:50]:
        name = c.get("name") or c.get("companyName") or ""
        code = c.get("code") or c.get("companyCode") or ""
        lines.append(f"- {name} ({code})")
    return "\n".join(lines)


def _format_order(order: Dict[str, Any]) -> str:
    """格式化单个订单（兼容pdd隐私字段null）"""
    tid = order.get("tid") or ""
    sid = order.get("sid") or ""
    status = order.get("sysStatus") or order.get("status") or ""
    buyer = order.get("buyerNick") or "（隐私保护）"
    payment = order.get("payment") or "0"
    shop = order.get("shopName") or ""
    source = order.get("source") or ""
    created = format_timestamp(order.get("created"))
    pay_time = format_timestamp(order.get("payTime"))

    line1 = f"- 订单号: {tid} | 系统单号: {sid}"
    line2 = f"  状态: {status} | 买家: {buyer} | 店铺: {shop}"
    if source:
        line2 += f" | 来源: {source}"
    line3 = f"  金额: ¥{payment} | 创建: {created} | 付款: {pay_time}"
    return f"{line1}\n{line2}\n{line3}"


def _format_shipment(item: Dict[str, Any]) -> str:
    """格式化出库/物流行（兼容pdd隐私字段null）"""
    tid = item.get("tid") or ""
    sid = item.get("sid") or ""
    status = item.get("sysStatus") or ""
    out_sid = item.get("outSid") or ""
    express = item.get("expressCompanyName") or ""
    shop = item.get("shopName") or ""
    consign_time = format_timestamp(item.get("consignTime"))
    payment = item.get("payment") or "0"
    warehouse = item.get("warehouseName") or ""

    lines = [f"- 订单: {tid} | 系统单号: {sid} | 状态: {status}"]
    if out_sid:
        lines.append(f"  快递: {express} | 单号: {out_sid}")
    line3 = f"  店铺: {shop} | 金额: ¥{payment} | 发货: {consign_time}"
    if warehouse:
        line3 += f" | 仓库: {warehouse}"
    lines.append(line3)

    orders = item.get("orders") or []
    for sub in orders[:5]:
        title = sub.get("sysTitle") or sub.get("title") or ""
        num = sub.get("num", 0)
        lines.append(f"    · {title} x{num}")
    return "\n".join(lines)


# 导出注册表
TRADE_FORMATTERS: Dict[str, Callable] = {
    "format_order_list": format_order_list,
    "format_shipment_list": format_shipment_list,
    "format_outstock_order_list": format_outstock_order_list,
    "format_order_log": format_order_log,
    "format_express_list": format_express_list,
    "format_logistics_company": format_logistics_company,
}
