"""
语义参数 → filters DSL 转换 + 空结果/错误诊断。

从 department_agent.py 提取，解决文件/函数超阈值问题。
DepartmentAgent 通过 _params_to_filters / _diagnose_empty / _diagnose_error 调用。
"""
from __future__ import annotations

from loguru import logger


# ── 过滤映射常量（模块级，避免每次调用重建） ──

# 文本精确匹配：LLM语义参数名 → DB列名
TEXT_EQ_FIELDS: dict[str, str] = {
    "express_no": "express_no",
    "buyer_nick": "buyer_nick",
    "doc_code": "doc_code",
    "receiver_name": "receiver_name",
    "platform_refund_id": "platform_refund_id",
    "purchase_order_code": "purchase_order_code",
    "refund_express_no": "refund_express_no",
    "sku_code": "sku_outer_id",  # 语义名 → DB列名
}

# 文本模糊匹配：LLM语义参数名 → DB列名
TEXT_LIKE_FIELDS: dict[str, str] = {
    "shop_name": "shop_name",
    "supplier_name": "supplier_name",
    "warehouse_name": "warehouse_name",
    "express_company": "express_company",
    "refund_express_company": "refund_express_company",
    "refund_warehouse_name": "refund_warehouse_name",
    "item_name": "item_name",
    "receiver_state": "receiver_state",
    "receiver_city": "receiver_city",
    "creator_name": "creator_name",
    "remark": "remark",
    "buyer_message": "buyer_message",
    "text_reason": "text_reason",
    "receiver_address": "receiver_address",
    "receiver_district": "receiver_district",
    "reason": "reason",
    "sku_properties_name": "sku_properties_name",  # SKU 规格（颜色/尺码等）
}

# 枚举精确匹配：LLM语义参数名 → DB列名
ENUM_EQ_FIELDS: dict[str, str] = {
    "order_status": "order_status",
    "doc_status": "doc_status",
    "aftersale_type": "aftersale_type",
    "refund_status": "refund_status",
    "good_status": "good_status",
    "order_type": "order_type",
    "online_status": "online_status",      # 售后在线状态
    "handler_status": "handler_status",    # 售后处理状态
}

# 布尔/整数标记字段（truthy 时 → eq 1）
FLAG_FIELDS: list[str] = [
    "is_cancel", "is_refund", "is_exception",
    "is_halt", "is_urgent", "is_presell",
]

# ── 枚举值映射（中文/别名 → DB 实际值）──
# 同步层直接存 API 原始值（整数/英文枚举），LLM 提取中文，这里做归一化

# order_type: 中文 → 整数码（API type 字段）
ORDER_TYPE_NORMALIZE: dict[str, str] = {
    "普通": "0", "货到付款": "1", "平台": "3", "线下": "4",
    "预售": "6", "合并": "7", "拆分": "8", "加急": "9",
    "空包": "10", "门店": "12", "换货": "13", "补发": "14",
    "分销": "33", "出库单": "99",
}

# aftersale_type: 中文 → 整数码（API afterSaleType 字段）
AFTERSALE_TYPE_NORMALIZE: dict[str, str] = {
    "其他": "0",
    "已发货仅退款": "1", "仅退款": "1", "退款": "1",
    "退货": "2", "退货退款": "2",
    "补发": "3",
    "换货": "4",
    "未发货仅退款": "5", "发货前退款": "5",
    "拒收退货": "7", "拒收": "7",
    "档口退货": "8",
    "维修": "9",
}

# refund_status: 中文 → 整数码（API refundStatus 字段）
REFUND_STATUS_NORMALIZE: dict[str, str] = {
    "无退款": "0",
    "退款中": "1",
    "退款成功": "2", "已退款": "2",
    "退款关闭": "3", "退款失败": "3",
}

# good_status: 中文 → 整数码（API goodStatus 字段）
GOOD_STATUS_NORMALIZE: dict[str, str] = {
    "买家未发": "1", "买家未退货": "1", "未发货": "1",
    "买家已发": "2", "买家已退货": "2", "已发货": "2",
    "卖家已收": "3", "已签收": "3",
    "无需退货": "4",
}

# online_status (售后域): 中文 → 整数码（API onlineStatus 字段）
ONLINE_STATUS_NORMALIZE: dict[str, str] = {
    "待卖家同意": "2", "等待卖家同意": "2",
    "待买家退货": "3", "等待买家退货": "3",
    "待卖家确认收货": "4", "等待卖家确认": "4",
    "卖家拒绝退款": "5", "卖家拒绝": "5", "卖家拒绝补寄": "5",
    "退款关闭": "6",
    "退款成功": "7", "已退款": "7",
    "待发出换货": "8", "待补发": "8",
    "待买家收货": "9", "等待买家收货": "9",
    "换货关闭": "10", "补发关闭": "10",
    "换货成功": "11", "补发成功": "11",
}

# handler_status (售后域): 中文 → 整数码（API handlerStatus 字段）
HANDLER_STATUS_NORMALIZE: dict[str, str] = {
    "待处理": "-1", "未处理": "-1",
    "处理成功": "1", "已处理": "1",
    "处理失败": "2",
}

# doc_status (采购域): 中文 → 英文枚举（API sysStatus 字段）
PURCHASE_STATUS_NORMALIZE: dict[str, str] = {
    "待审核": "WAIT_VERIFY", "待验证": "WAIT_VERIFY",
    "审核中": "VERIFYING", "验证中": "VERIFYING",
    "已审核": "VERIFYING",  # 审核通过后进入下一环节
    "待收货": "GOODS_NOT_ARRIVED", "未到货": "GOODS_NOT_ARRIVED",
    "部分到货": "GOODS_PART_ARRIVED",
    "已完成": "FINISHED", "已收货": "FINISHED",
    "已关闭": "GOODS_CLOSED", "关闭": "GOODS_CLOSED",
}

# 汇总：param_key → (db_field, normalize_map)
# _params_to_filters 中的 ENUM_EQ_FIELDS 会查这个表做归一化
ENUM_NORMALIZE: dict[str, dict[str, str]] = {
    "order_type": ORDER_TYPE_NORMALIZE,
    "aftersale_type": AFTERSALE_TYPE_NORMALIZE,
    "refund_status": REFUND_STATUS_NORMALIZE,
    "good_status": GOOD_STATUS_NORMALIZE,
    "doc_status": PURCHASE_STATUS_NORMALIZE,
    "online_status": ONLINE_STATUS_NORMALIZE,
    "handler_status": HANDLER_STATUS_NORMALIZE,
    # order_status 不需要归一化（LLM 直接输出英文枚举）
}

# 空结果诊断模板：field → 提示文本（{v} 替换为值）
DIAG_MAP: dict[str, str] = {
    "order_no": "订单号 {v} 未匹配到记录，请确认号码是否正确",
    "outer_id": "商品编码 {v} 未匹配到记录，请确认编码是否正确",
    "express_no": "快递单号 {v} 未匹配到记录，请确认单号是否正确",
    "buyer_nick": "买家昵称 {v} 未匹配到记录，请确认是否正确",
    "shop_name": "当前过滤了店铺={v}，可尝试不限店铺查询",
    "supplier_name": "当前过滤了供应商={v}，可尝试不限供应商查询",
    "warehouse_name": "当前过滤了仓库={v}，可尝试不限仓库查询",
    "order_status": "当前过滤了订单状态={v}，可尝试不限状态查询",
    "doc_status": "当前过滤了单据状态={v}，可尝试不限状态查询",
    "aftersale_type": "当前过滤了售后类型={v}，可尝试不限类型查询",
    "refund_status": "当前过滤了退款状态={v}，可尝试不限状态查询",
    "receiver_state": "当前过滤了收件省={v}，可尝试不限地区查询",
    "receiver_city": "当前过滤了收件市={v}，可尝试不限地区查询",
    "doc_code": "单据编号 {v} 未匹配到记录，请确认编号是否正确",
    "sku_outer_id": "SKU编码 {v} 未匹配到记录，请确认编码是否正确",
}


# ── 转换函数 ──


def params_to_filters(params: dict) -> list[dict]:
    """把 PlanBuilder 输出的语义参数转成 UnifiedQueryEngine 的 filters DSL。

    语义参数（LLM 输出）：time_range / time_col / platform / ...
    filters DSL（执行层）：[{field, op, value}]

    这一步是确定性转换，不需要 LLM。
    """
    filters: list[dict] = []

    # ── 时间范围 → gte/lt ──
    tr = params.get("time_range")
    if tr and isinstance(tr, str):
        for alt in (" to ", "～"):
            if alt in tr:
                tr = tr.replace(alt, "~")
                logger.info(f"L1 time_range 分隔符纠正: {alt!r} → '~'")
                break
    if tr and "~" in tr:
        time_col = params.get("time_col", "doc_created_at")
        parts = tr.split("~")
        if len(parts) == 2:
            start = parts[0].strip()
            end = parts[1].strip()
            has_start_time = " " in start
            has_end_time = " " in end
            if start:
                start_val = (
                    start.replace(" ", "T") if has_start_time
                    else f"{start}T00:00:00"
                )
                filters.append({
                    "field": time_col, "op": "gte", "value": start_val,
                })
            if end:
                if has_end_time:
                    filters.append({
                        "field": time_col, "op": "lt",
                        "value": end.replace(" ", "T"),
                    })
                else:
                    try:
                        from datetime import date as _date, timedelta as _td
                        next_day = (
                            _date.fromisoformat(end) + _td(days=1)
                        ).isoformat()
                    except ValueError:
                        next_day = end
                    filters.append({
                        "field": time_col, "op": "lt",
                        "value": f"{next_day}T00:00:00",
                    })

    # ── 平台 → eq（编码映射） ──
    platform = params.get("platform")
    if isinstance(platform, str):
        platform = platform.strip()
    if platform:
        from services.kuaimai.erp_unified_schema import PLATFORM_NORMALIZE
        normalized = PLATFORM_NORMALIZE.get(platform, platform)
        if normalized != platform:
            logger.info(f"L1 platform 映射: {platform!r} → {normalized!r}")
        filters.append({"field": "platform", "op": "eq", "value": normalized})

    # ── 订单号 → eq ──
    order_no = params.get("order_no")
    if isinstance(order_no, str):
        order_no = order_no.strip()
    if order_no:
        filters.append({"field": "order_no", "op": "eq", "value": order_no})

    # ── 商品编码 → outer_id eq ──
    product_code = params.get("product_code")
    if isinstance(product_code, str):
        product_code = product_code.strip()
    if product_code:
        filters.append({"field": "outer_id", "op": "eq", "value": product_code})

    # ── 刷单筛选 ──
    if params.get("is_scalping"):
        filters.append({"field": "is_scalping", "op": "eq", "value": 1})

    # ── 批量：文本精确匹配 ──
    for param_key, db_field in TEXT_EQ_FIELDS.items():
        val = params.get(param_key)
        if isinstance(val, str):
            val = val.strip()
        if val:
            filters.append({"field": db_field, "op": "eq", "value": val})

    # ── 批量：文本模糊匹配 ──
    # 空格→%：LLM 常在中文和数字间插入噪音空格（"纸制品 01"），
    # 转为通配符使 ILIKE '%纸制品%01%' 同时兼容有/无空格的真实数据。
    for param_key, db_field in TEXT_LIKE_FIELDS.items():
        val = params.get(param_key)
        if isinstance(val, str):
            val = val.strip()
        if val:
            like_val = "%" + val.replace(" ", "%") + "%"
            filters.append({
                "field": db_field, "op": "like", "value": like_val,
            })

    # ── 批量：枚举精确匹配（中文 → DB 值归一化） ──
    for param_key, db_field in ENUM_EQ_FIELDS.items():
        val = params.get(param_key)
        if isinstance(val, str):
            val = val.strip()
        if val:
            norm_map = ENUM_NORMALIZE.get(param_key)
            if norm_map:
                normalized = norm_map.get(val)
                if normalized is not None:
                    logger.info(
                        f"枚举归一化: {param_key}={val!r} → {normalized!r}",
                    )
                    val = normalized
                # 未找到映射时保留原值（可能用户直接输了 DB 值如 "WAIT_SEND_GOODS"）
            filters.append({"field": db_field, "op": "eq", "value": val})

    # ── 批量：布尔标记 ──
    for flag in FLAG_FIELDS:
        if params.get(flag):
            filters.append({"field": flag, "op": "eq", "value": 1})

    return filters


def diagnose_empty(filters: list[dict]) -> str:
    """L3：查询返回空结果时，根据 filters 生成诊断建议。"""
    hints: list[str] = []
    for f in filters:
        field, value = f.get("field", ""), f.get("value", "")
        if not value:
            continue
        if field == "platform":
            from services.kuaimai.erp_unified_schema import PLATFORM_CN
            cn = PLATFORM_CN.get(value, value)
            hints.append(f"当前过滤了平台={cn}，可尝试不限平台查询")
        elif field in DIAG_MAP:
            display_val = str(value).strip("%")
            hints.append(DIAG_MAP[field].format(v=display_val))
    return "\n".join(f"- {h}" for h in hints) if hints else ""


def diagnose_error(error_msg: str) -> str:
    """L3：查询失败时，根据错误信息给出诊断描述（中性，不引导重试）。"""
    if not error_msg:
        return ""
    msg = error_msg.lower()
    if "timeout" in msg or "超时" in msg:
        return "查询超时，可能原因：时间范围过大 / 数据量过多"
    if "too many" in msg or "65535" in msg or "参数" in msg:
        return "数据量超出处理能力，可能原因：时间范围过大 / 过滤条件不足"
    if "invalid" in msg and "doc_type" in msg:
        return "文档类型不正确，请确认查询类型"
    if "no valid" in msg and "field" in msg:
        return "字段名无效，请参考可用字段列表"
    if "filter" in msg or "column" in msg:
        return "过滤条件有误，请检查字段名和操作符"
    return ""
