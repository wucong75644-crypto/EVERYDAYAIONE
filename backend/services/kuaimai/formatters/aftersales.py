"""
售后 格式化器（Phase 5B 标签映射表模式）

格式化售后工单、退货入库、维修单、补款、日志等查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_item_with_labels, format_timestamp
from services.kuaimai.registry.base import ApiEntry

# ---------------------------------------------------------------------------
# 售后工单 — erp.aftersale.work.order.list.query
# 修正: refundId→id / refundFee→refundMoney
# ---------------------------------------------------------------------------
_AFTERSALE_LABELS = {
    "id": "工单号",
    "shortId": "短号",
    "tid": "订单号", "sid": "系统单号",
    "afterSaleType": "类型",
    "status": "状态",
    "buyerNick": "买家",
    "buyerName": "买家姓名", "buyerPhone": "买家电话",
    "shopName": "店铺", "source": "平台",
    "refundMoney": "系统退款",
    "rawRefundMoney": "平台实退",
    "refundPostFee": "退运费",
    "goodStatus": "货物状态",
    "textReason": "原因",
    "refundWarehouseName": "退货仓库",
    "refundExpressCompany": "退回快递",
    "refundExpressId": "退回单号",
    "platformId": "平台售后单号",
    "reissueSid": "补发/换货订单号",
    "remark": "备注",
    "created": "创建时间",
    "finished": "完成时间",
}
_AFTERSALE_TRANSFORMS: Dict[str, Callable] = {
    "afterSaleType": lambda v: {1: "退款", 2: "退货", 3: "补发",
                                4: "换货", 5: "发货前退款"}.get(v, str(v)),
    "status": lambda v: {1: "未分配", 2: "未解决", 3: "优先退款", 4: "同意",
                         5: "拒绝", 6: "确认退货", 7: "确认发货", 8: "确认退款",
                         9: "处理完成", 10: "作废"}.get(v, str(v)),
    "goodStatus": lambda v: {1: "买家未发", 2: "买家已发",
                             3: "卖家已收", 4: "无需退货"}.get(v, str(v)),
    "refundMoney": lambda v: f"¥{v}" if v else "",
    "rawRefundMoney": lambda v: f"¥{v}" if v else "",
    "refundPostFee": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
    "finished": format_timestamp,
}

# 售后工单嵌套商品
_AFTERSALE_ITEM_LABELS = {
    "title": "商品", "mainOuterId": "主编码", "outerId": "编码",
    "propertiesName": "规格",
    "receivableCount": "申请数", "itemRealQty": "实退数",
    "price": "单价", "payment": "实付",
    "type": "处理方式",
    "goodItemCount": "良品数", "badItemCount": "次品数",
}
_AFTERSALE_ITEM_TRANSFORMS: Dict[str, Callable] = {
    "type": lambda v: {1: "退货", 2: "补发"}.get(v, str(v)),
    "price": lambda v: f"¥{v}" if v else "",
    "payment": lambda v: f"¥{v}" if v else "",
}

# ---------------------------------------------------------------------------
# 销退入库单 — erp.aftersale.refund.warehouse.query
# 修正: orderNo→id / warehouseName→wareHouseName
# ---------------------------------------------------------------------------
_REFUND_WH_LABELS = {
    "id": "入库单号",
    "workOrderId": "售后工单号",
    "sid": "系统单号", "tid": "订单号",
    "afterSaleTypeName": "售后类型",
    "wareHouseName": "收货仓库",
    "status": "状态",
    "receiveUser": "收货人",
    "receiveGoodsTime": "收货时间",
    "expressName": "退回快递",
    "expressId": "退回快递号",
    "endTime": "完成时间",
}
_REFUND_WH_TRANSFORMS: Dict[str, Callable] = {
    "status": lambda v: {1: "待入库", 2: "部分入库", 3: "已完成",
                         4: "已取消", 5: "已作废"}.get(v, str(v)),
    "receiveGoodsTime": format_timestamp,
    "endTime": format_timestamp,
}

# ---------------------------------------------------------------------------
# 登记补款 — erp.aftersale.replenish.list.query
# 修正: amount→refundMoney
# ---------------------------------------------------------------------------
_REPLENISH_LABELS = {
    "tid": "订单号", "sid": "系统单号",
    "shopName": "店铺",
    "afterSaleType": "售后类型",
    "refundMoney": "补款金额",
    "status": "状态",
    "urgency": "紧急程度",
    "sysMaker": "创建人",
    "responsiblePerson": "责任人",
    "replenishRemark": "备注",
    "created": "创建时间",
}
_REPLENISH_TRANSFORMS: Dict[str, Callable] = {
    "refundMoney": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
}

# ---------------------------------------------------------------------------
# 维修单列表 — erp.aftersale.repair.list.query
# 修正: repairNo→repairOrderNum / customerName→userNick / status→repairStatus
# ---------------------------------------------------------------------------
_REPAIR_LABELS = {
    "repairOrderNum": "维修单号",
    "repairStatus": "状态",
    "userNick": "用户",
    "sid": "系统单号", "tid": "订单号",
    "shopName": "店铺",
    "contactInfo": "联系方式",
    "repairMoney": "维修费用",
    "repairWarehouseName": "维修仓库",
    "problemDescription": "问题描述",
    "failureCause": "故障原因",
    "created": "创建时间",
    "finishTime": "完成时间",
}
_REPAIR_TRANSFORMS: Dict[str, Callable] = {
    "repairStatus": lambda v: {0: "待受理", 1: "维修中", 2: "待出库",
                               3: "已完成", 4: "已拒绝", -1: "已作废"}.get(v, str(v)),
    "repairMoney": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
    "finishTime": format_timestamp,
}

# ---------------------------------------------------------------------------
# 维修单详情 — erp.aftersale.repair.detail.query
# 结构: {order: {...}, itemList: [...], feeList: [...], partsList: [...]}
# ---------------------------------------------------------------------------
_REPAIR_DETAIL_ITEM_LABELS = {
    "repairItemName": "商品名",
    "repairItemCode": "编码",
    "specification": "规格",
    "repairQuantity": "数量",
    "identificationCode": "识别码",
    "problemDescription": "问题描述",
}
_REPAIR_DETAIL_FEE_LABELS = {
    "currentPrice": "费用",
    "receivedWay": "入账途径",
    "operatorName": "操作人",
    "operatorTime": "操作时间",
}
_REPAIR_DETAIL_FEE_TRANSFORMS: Dict[str, Callable] = {
    "currentPrice": lambda v: f"¥{v}" if v else "",
    "operatorTime": format_timestamp,
}

# ---------------------------------------------------------------------------
# 售后操作日志 — erp.aftersale.operate.log.query
# 修正: operTime→operateTime / action→content / operName→staffName / operator→operateName
# ---------------------------------------------------------------------------
_AFTERSALE_LOG_LABELS = {
    "key": "工单号",
    "operateTime": "时间",
    "operateType": "操作类型",
    "content": "操作内容",
    "staffName": "操作人账号",
    "operateName": "操作人",
}
_AFTERSALE_LOG_TRANSFORMS: Dict[str, Callable] = {
    "operateTime": format_timestamp,
}


# ===== 公开 formatter 函数 =====

def format_aftersale_list(data: Any, entry: ApiEntry) -> str:
    """售后工单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到售后工单"
    lines = [f"共 {total} 条售后工单：\n"]
    for item in items[:20]:
        # 主信息
        main = "- " + format_item_with_labels(
            item, _AFTERSALE_LABELS, transforms=_AFTERSALE_TRANSFORMS)
        # 嵌套商品
        sub_lines = []
        for sub in (item.get("items") or [])[:3]:
            sub_lines.append("    · " + format_item_with_labels(
                sub, _AFTERSALE_ITEM_LABELS,
                transforms=_AFTERSALE_ITEM_TRANSFORMS))
        if sub_lines:
            main += "\n" + "\n".join(sub_lines)
        lines.append(main)
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
        lines.append("- " + format_item_with_labels(
            item, _REFUND_WH_LABELS, transforms=_REFUND_WH_TRANSFORMS))
    return "\n".join(lines)


def format_replenish_list(data: Any, entry: ApiEntry) -> str:
    """登记补款列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到补款记录"
    lines = [f"共 {total} 条补款记录：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _REPLENISH_LABELS, transforms=_REPLENISH_TRANSFORMS))
    return "\n".join(lines)


def format_repair_list(data: Any, entry: ApiEntry) -> str:
    """维修单列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到维修单"
    lines = [f"共 {total} 条维修单：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _REPAIR_LABELS, transforms=_REPAIR_TRANSFORMS))
    return "\n".join(lines)


def format_repair_detail(data: Any, entry: ApiEntry) -> str:
    """维修单详情"""
    # API返回 {order, itemList, feeList, partsList}
    order = data.get("order") or data
    header = format_item_with_labels(
        order, _REPAIR_LABELS, transforms=_REPAIR_TRANSFORMS)
    lines = [f"维修单详情: {header}"]

    # 维修商品
    item_list = data.get("itemList") or data.get("items") or data.get("details") or []
    if item_list:
        lines.append(f"\n维修商品（共{len(item_list)}个）：")
        for it in item_list[:10]:
            lines.append("  - " + format_item_with_labels(
                it, _REPAIR_DETAIL_ITEM_LABELS))

    # 费用明细
    fee_list = data.get("feeList") or []
    if fee_list:
        lines.append(f"\n费用明细（共{len(fee_list)}个）：")
        for fee in fee_list[:10]:
            lines.append("  - " + format_item_with_labels(
                fee, _REPAIR_DETAIL_FEE_LABELS,
                transforms=_REPAIR_DETAIL_FEE_TRANSFORMS))

    return "\n".join(lines)


def format_aftersale_log(data: Any, entry: ApiEntry) -> str:
    """售后操作日志"""
    items = data.get("list") or []
    if not items:
        return "未找到售后操作日志"
    lines = [f"共 {len(items)} 条操作日志：\n"]
    for log in items[:30]:
        lines.append("- " + format_item_with_labels(
            log, _AFTERSALE_LOG_LABELS, transforms=_AFTERSALE_LOG_TRANSFORMS))
    return "\n".join(lines)


AFTERSALES_FORMATTERS: Dict[str, Callable] = {
    "format_aftersale_list": format_aftersale_list,
    "format_refund_warehouse": format_refund_warehouse,
    "format_replenish_list": format_replenish_list,
    "format_repair_list": format_repair_list,
    "format_repair_detail": format_repair_detail,
    "format_aftersale_log": format_aftersale_log,
}
