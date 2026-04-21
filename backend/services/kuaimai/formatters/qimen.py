"""
奇门接口格式化器（Phase 5B 标签映射表模式）

格式化淘宝奇门接口（kuaimai.order.list.query / kuaimai.refund.list.query）
的响应数据为 Agent 大脑可读的文本。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_item_with_labels, format_platform, format_timestamp
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

# ---------------------------------------------------------------------------
# 淘宝订单 — kuaimai.order.list.query
# 补全收件人/物流字段
# ---------------------------------------------------------------------------
_QIMEN_ORDER_LABELS = {
    "tid": "订单号", "sid": "系统单号",
    "type": "类型",
    "sysStatus": "状态", "chSysStatus": "中文状态",
    "buyerNick": "买家",
    "payment": "付款金额", "payAmount": "实付金额",
    "postFee": "运费",
    "shopName": "店铺", "source": "来源", "warehouseName": "仓库",
    "outSid": "快递单号",
    "receiverName": "收件人", "receiverMobile": "电话",
    "receiverState": "省", "receiverCity": "市",
    "receiverAddress": "地址",
    "sellerMemo": "卖家备注", "buyerMessage": "买家留言",
    "created": "下单时间", "payTime": "付款时间",
    "consignTime": "发货时间",
}
_QIMEN_ORDER_TRANSFORMS: Dict[str, Callable] = {
    "source": format_platform,
    "type": lambda v: _ORDER_TYPE_MAP.get(str(v), str(v)) if v is not None else "",
    "buyerNick": lambda v: v or "（隐私保护）",
    "payment": lambda v: f"¥{v}" if v else "",
    "payAmount": lambda v: f"¥{v}" if v else "",
    "postFee": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp, "payTime": format_timestamp,
    "consignTime": format_timestamp,
}

# 淘宝订单子订单
_QIMEN_SUB_ORDER_LABELS = {
    "sysTitle": "商品", "title": "商品",
    "sysOuterId": "编码", "outerId": "编码",
    "num": "数量",
}

# ---------------------------------------------------------------------------
# 淘宝售后工单 — kuaimai.refund.list.query
# 补全退款/物流字段
# ---------------------------------------------------------------------------
_QIMEN_REFUND_LABELS = {
    "id": "工单号",
    "tid": "订单号", "sid": "系统单号",
    "afterSaleType": "类型",
    "status": "状态",
    "shopName": "店铺", "source": "平台",
    "buyerName": "买家姓名", "buyerPhone": "买家电话",
    "refundMoney": "系统退款",
    "rawRefundMoney": "平台实退",
    "refundPostFee": "退运费",
    "goodStatus": "货物状态",
    "textReason": "原因",
    "refundWarehouseName": "退货仓库",
    "refundExpressCompany": "退回快递", "refundExpressId": "退回单号",
    "platformId": "平台售后单号",
    "reissueSid": "补发订单号",
    "remark": "备注",
    "created": "创建时间",
    "finished": "完成时间",
}
_QIMEN_REFUND_TRANSFORMS: Dict[str, Callable] = {
    "source": format_platform,
    "afterSaleType": lambda v: _REFUND_TYPE_MAP.get(v, str(v)),
    "status": lambda v: _REFUND_STATUS_MAP.get(v, str(v)),
    "goodStatus": lambda v: {1: "买家未发", 2: "买家已发",
                             3: "卖家已收", 4: "无需退货"}.get(v, str(v)),
    "refundMoney": lambda v: f"¥{v}" if v else "",
    "rawRefundMoney": lambda v: f"¥{v}" if v else "",
    "refundPostFee": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
    "finished": format_timestamp,
}

# 售后嵌套商品
_QIMEN_REFUND_ITEM_LABELS = {
    "title": "商品", "outerId": "编码",
    "receivableCount": "申请数",
}


# ===== 公开 formatter 函数 =====

def format_qimen_order_list(data: Any, entry: ApiEntry) -> str:
    """淘宝订单列表（trades key）"""
    items = data.get("trades") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的淘宝订单"
    lines = [f"共找到 {total} 条淘宝订单：\n"]
    for order in items[:20]:
        main = "- " + format_item_with_labels(
            order, _QIMEN_ORDER_LABELS, transforms=_QIMEN_ORDER_TRANSFORMS)
        # 商品明细
        sub_lines = []
        for sub in (order.get("orders") or [])[:3]:
            sub_lines.append("    · " + format_item_with_labels(
                sub, _QIMEN_SUB_ORDER_LABELS))
        if sub_lines:
            main += "\n" + "\n".join(sub_lines)
        lines.append(main)
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
        main = "- " + format_item_with_labels(
            wo, _QIMEN_REFUND_LABELS, transforms=_QIMEN_REFUND_TRANSFORMS)
        # 售后商品明细
        sub_lines = []
        for item in (wo.get("items") or [])[:3]:
            sub_lines.append("    · " + format_item_with_labels(
                item, _QIMEN_REFUND_ITEM_LABELS))
        if sub_lines:
            main += "\n" + "\n".join(sub_lines)
        lines.append(main)
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


QIMEN_FORMATTERS: Dict[str, Callable] = {
    "format_qimen_order_list": format_qimen_order_list,
    "format_qimen_refund_list": format_qimen_refund_list,
}

# 返回字段注册表（供 erp_api_search 生成文档）
QIMEN_RESPONSE_FIELDS: Dict[str, Dict] = {
    "format_qimen_order_list": {
        "main": _QIMEN_ORDER_LABELS,
        "items": _QIMEN_SUB_ORDER_LABELS,
        "items_key": "orders",
    },
    "format_qimen_refund_list": {
        "main": _QIMEN_REFUND_LABELS,
        "items": _QIMEN_REFUND_ITEM_LABELS,
        "items_key": "items",
    },
}
