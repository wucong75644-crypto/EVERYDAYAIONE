"""
交易/物流 格式化器（Phase 5B 标签映射表模式）

从 service.py 迁移: _format_order, _format_shipment
新增: 操作日志、快递单号、波次等格式化
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_item_with_labels, format_timestamp
from services.kuaimai.registry.base import ApiEntry

# ---------------------------------------------------------------------------
# 订单 — erp.trade.list.query / erp.trade.detail.query
# 修正: receiverProvince → receiverState
# ---------------------------------------------------------------------------
_ORDER_LABELS = {
    "tid": "订单号", "sid": "系统单号",
    "type": "订单类型",
    "sysStatus": "状态", "buyerNick": "买家",
    "payment": "付款金额", "payAmount": "实付金额",
    "cost": "成本", "grossProfit": "毛利",
    "postFee": "运费", "discountFee": "折扣",
    "shopName": "店铺", "source": "平台", "warehouseName": "仓库",
    "outSid": "快递单号", "expressCompanyName": "快递公司",
    "created": "下单时间", "payTime": "付款时间", "consignTime": "发货时间",
    "sellerMemo": "卖家备注", "buyerMessage": "买家留言", "sysMemo": "系统备注",
    "isRefund": "退款", "isExcep": "异常", "isHalt": "挂起",
    "isCancel": "取消", "isUrgent": "加急",
    "receiverName": "收件人",
    "receiverMobile": "手机", "receiverPhone": "电话",
    "receiverState": "省", "receiverCity": "市",
    "receiverDistrict": "区", "receiverAddress": "地址",
}
_ORDER_TRANSFORMS: Dict[str, Callable] = {
    "type": lambda v: {0: "普通", 7: "合并", 8: "拆分", 33: "分销",
                       99: "出库单"}.get(v, str(v)) if isinstance(v, int) else str(v),
    "buyerNick": lambda v: v or "（隐私保护）",
    "created": format_timestamp, "payTime": format_timestamp,
    "consignTime": format_timestamp,
    "isRefund": lambda v: "是" if v == 1 else "",
    "isExcep": lambda v: "是" if v == 1 else "",
    "isHalt": lambda v: "是" if v == 1 else "",
    "isCancel": lambda v: "是" if v == 1 else "",
    "isUrgent": lambda v: "是" if v == 1 else "",
    "payment": lambda v: f"¥{v}" if v else "",
    "payAmount": lambda v: f"¥{v}" if v else "",
    "cost": lambda v: f"¥{v}" if v else "",
    "grossProfit": lambda v: f"¥{v}" if v else "",
    "postFee": lambda v: f"¥{v}" if v else "",
    "discountFee": lambda v: f"¥{v}" if v else "",
}

# 子订单
_SUB_ORDER_LABELS = {
    "sysTitle": "商品", "sysOuterId": "编码", "outerSkuId": "SKU编码",
    "skuPropertiesName": "规格",
    "num": "数量", "diffStockNum": "缺货数量",
    "price": "单价", "payment": "实付",
    "cost": "成本", "refundStatus": "退款状态",
}
_SUB_ORDER_TRANSFORMS: Dict[str, Callable] = {
    "price": lambda v: f"¥{v}" if v else "",
    "payment": lambda v: f"¥{v}" if v else "",
    "cost": lambda v: f"¥{v}" if v else "",
}

# ---------------------------------------------------------------------------
# 操作日志 — erp.trade.trace.list
# 修正: operTime→operateTime / operName→operator / remark→content
# ---------------------------------------------------------------------------
_ORDER_LOG_LABELS = {
    "sid": "系统单号",
    "operateTime": "时间",
    "action": "操作",
    "operator": "操作人",
    "content": "内容",
}
_ORDER_LOG_TRANSFORMS: Dict[str, Callable] = {
    "operateTime": format_timestamp,
}

# ---------------------------------------------------------------------------
# 物流公司 — erp.trade.logistics.company.user.list
# 修正: code/companyCode → cpCode
# ---------------------------------------------------------------------------
_LOGISTICS_COMPANY_LABELS = {
    "name": "公司名称",
    "cpCode": "快递编码",
    "cpType": "服务类型",
    "liveStatus": "状态",
    "id": "ID",
}
_LOGISTICS_COMPANY_TRANSFORMS: Dict[str, Callable] = {
    "cpType": lambda v: {1: "直营", 2: "加盟", 3: "落地配",
                         4: "直营+网点"}.get(int(v), str(v)) if v else str(v),
    "liveStatus": lambda v: "启用" if v == 1 else "停用",
}


# ===== 公开 formatter 函数 =====

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
        lines.append("- " + format_item_with_labels(
            log, _ORDER_LOG_LABELS, transforms=_ORDER_LOG_TRANSFORMS))
    return "\n".join(lines)


def format_express_list(data: Any, entry: ApiEntry) -> str:
    """多快递单号查询 — erp.trade.multi.packs.query

    API返回扁平结构 {cpCode, outSids[], expressName}，非列表。
    """
    # 兼容两种结构
    if isinstance(data.get("list"), list):
        # 旧结构兜底
        items = data["list"]
        if not items:
            return "未找到快递信息"
        lines = [f"共 {len(items)} 条快递记录：\n"]
        for item in items[:20]:
            lines.append("- " + format_item_with_labels(item, {
                "tid": "订单", "outSid": "单号",
                "expressCompanyName": "快递公司",
            }))
        return "\n".join(lines)

    # 扁平结构（API实际格式）
    cp_code = data.get("cpCode") or ""
    out_sids = data.get("outSids") or []
    express_name = data.get("expressName") or ""
    if not out_sids:
        return "未找到快递信息"
    lines = [f"快递公司: {express_name} ({cp_code})"]
    for sid in out_sids:
        lines.append(f"  - 单号: {sid}")
    return "\n".join(lines)


def format_logistics_company(data: Any, entry: ApiEntry) -> str:
    """物流公司列表"""
    items = data.get("list") or []
    if not items:
        return "暂无物流公司信息"
    lines = [f"共 {len(items)} 家物流公司：\n"]
    for c in items[:50]:
        lines.append("- " + format_item_with_labels(
            c, _LOGISTICS_COMPANY_LABELS,
            transforms=_LOGISTICS_COMPANY_TRANSFORMS))
    return "\n".join(lines)


def _format_order(order: Dict[str, Any]) -> str:
    """格式化单个订单"""
    main = "- " + format_item_with_labels(
        order, _ORDER_LABELS, transforms=_ORDER_TRANSFORMS)

    # 子订单商品明细
    sub_lines = []
    for sub in (order.get("orders") or [])[:5]:
        sub_lines.append("    · " + format_item_with_labels(
            sub, _SUB_ORDER_LABELS, transforms=_SUB_ORDER_TRANSFORMS))

    if sub_lines:
        return main + "\n" + "\n".join(sub_lines)
    return main


def _format_shipment(item: Dict[str, Any]) -> str:
    """格式化出库/物流行"""
    # statusName 优先（中文状态如"已发货"），覆盖 sysStatus 英文码
    if item.get("statusName"):
        item = {**item, "sysStatus": item["statusName"]}
    main = "- " + format_item_with_labels(
        item, _ORDER_LABELS, transforms=_ORDER_TRANSFORMS)

    # 兼容 orders[] 和 details[] 两种嵌套键
    sub_items = item.get("orders") or item.get("details") or []
    sub_lines = []
    for sub in sub_items[:5]:
        title = (sub.get("sysTitle") or sub.get("title")
                 or sub.get("itemOuterId") or "")
        num = sub.get("num", 0)
        outer_id = sub.get("sysOuterId") or sub.get("outerId") or ""
        price = sub.get("price")
        payment = sub.get("payment")
        parts = [f"    · {title} x{num}"]
        if outer_id:
            parts.append(f"编码: {outer_id}")
        if price is not None:
            parts.append(f"单价: ¥{price}")
        if payment is not None:
            parts.append(f"实付: ¥{payment}")
        sub_lines.append(" | ".join(parts))

    if sub_lines:
        return main + "\n" + "\n".join(sub_lines)
    return main


# 导出注册表
TRADE_FORMATTERS: Dict[str, Callable] = {
    "format_order_list": format_order_list,
    "format_shipment_list": format_shipment_list,
    "format_outstock_order_list": format_outstock_order_list,
    "format_order_log": format_order_log,
    "format_express_list": format_express_list,
    "format_logistics_company": format_logistics_company,
}
