"""
ERP 查询参数提取工具函数。

提供 ERPAgent 需要的：
- 关键词路由（quick_classify）
- 参数校验（_sanitize_params）
- LLM prompt 构建与解析（build_extract_prompt, parse_extract_response）
- 降级参数构造（_build_fallback_params）

拆分到独立模块（此处 re-export 保持兼容）：
- plan_fill.py: fill_platform, _fill_codes_for_params
- erp_tool_description.py: get_capability_manifest, build_tool_description

设计文档: docs/document/TECH_ERPAgent架构简化.md §3.1 / §6
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from loguru import logger


# ── 关键词 → 域映射（降级用）──

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "warehouse": [
        "库存", "缺货", "可售", "锁定", "在途", "仓库", "入库",
        "上架", "盘点", "调拨", "stock", "inventory",
    ],
    "purchase": [
        "采购", "到货", "供应商", "采退", "purchase", "supplier",
    ],
    "trade": [
        "订单", "发货", "物流", "快递", "签收", "退款",
        "order", "trade", "logistics",
    ],
    "aftersale": [
        "退货", "售后", "退款率", "退货率", "换货",
        "aftersale", "return",
    ],
}

# 有效域名（不含 compute）
VALID_DOMAINS = frozenset({
    "warehouse", "purchase", "trade", "aftersale",
})

# L2 域路由冲突检测：agent → 允许的 doc_type 集合
_DOMAIN_DOC_TYPES: dict[str, frozenset[str]] = {
    "trade": frozenset({"order", "order_log"}),
    "purchase": frozenset({"purchase", "purchase_return"}),
    "aftersale": frozenset({"aftersale", "aftersale_log"}),
    "warehouse": frozenset({"receipt", "shelf", "stock", "batch_stock",
                             "product", "sku", "daily_stats", "platform_map"}),
}
# 域路由冲突时的默认 doc_type
_DOMAIN_DEFAULT_DOC_TYPE: dict[str, str] = {
    "trade": "order",
    "purchase": "purchase",
    "aftersale": "aftersale",
    "warehouse": "receipt",
}


def quick_classify(query: str) -> str | None:
    """关键词匹配单域分类（降级链第二级）。

    返回域名（如 "warehouse"）或 None。
    并列得分时返回 None（歧义，应由 LLM 第一级处理）。
    """
    query_lower = query.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in query_lower)
        if score > 0:
            scores[domain] = score

    if not scores:
        return None
    sorted_scores = sorted(
        scores.items(), key=lambda x: x[1], reverse=True,
    )
    if (
        len(sorted_scores) >= 2
        and sorted_scores[0][1] == sorted_scores[1][1]
    ):
        logger.info(
            f"quick_classify ambiguous: {sorted_scores[:3]}",
        )
        return None
    return sorted_scores[0][0]


# 公开常量（供 get_capability_manifest / 外部引用）
VALID_MODES = frozenset({"summary", "export"})
VALID_DOC_TYPES = frozenset({
    "order", "purchase", "purchase_return", "aftersale",
    "receipt", "shelf",
    "stock", "product", "sku", "daily_stats", "platform_map",
    "batch_stock", "order_log", "aftersale_log",
})
# 向后兼容旧名
_VALID_MODES = VALID_MODES
_VALID_DOC_TYPES = VALID_DOC_TYPES
_TIME_RANGE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?\s*~\s*\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?$",
)
# _sanitize_params 中已做特殊校验/变换的参数，透传逻辑跳过这些 key
_COMPLEX_KEYS = frozenset({"mode", "doc_type", "time_range", "group_by", "extra_fields",
                            "fields"})  # fields: 向后兼容旧名，映射到 extra_fields
# list[dict] 类型的参数白名单（_sanitize_params 需特殊处理）
_LIST_DICT_PARAMS = frozenset({"numeric_filters", "exclude_filters"})


def _sanitize_params(params: dict) -> dict:
    """宽容校验参数：复杂类型严格校验，简单类型（str/bool）透传。

    设计原则：只对需要变换/枚举校验的参数做特殊处理，
    其余 LLM 提取的参数直接透传给下游（下游 _params_to_filters /
    execute() 自行决定是否使用，未知参数被 **_kwargs 吸收）。
    新增简单参数只需改 build_extract_prompt，不用改这里。
    """
    if not isinstance(params, dict):
        return {}
    clean: dict = {}

    # ── 需要校验/变换的复杂参数 ──
    mode = params.get("mode", "summary")
    if mode == "detail":
        mode = "export"  # detail 已合并到 export（staging + profile 统一处理）
    clean["mode"] = mode if mode in _VALID_MODES else "summary"

    doc_type = params.get("doc_type")
    if doc_type and doc_type in _VALID_DOC_TYPES:
        clean["doc_type"] = doc_type

    tr = params.get("time_range")
    if tr and isinstance(tr, str) and _TIME_RANGE_RE.match(tr.strip()):
        clean["time_range"] = tr.strip()

    # group_by: 标量字符串转列表（execute() 期望 list[str]）
    if params.get("group_by"):
        gb = params["group_by"]
        clean["group_by"] = [gb] if isinstance(gb, str) else gb

    # extra_fields: 追加列（白名单校验）
    # 语义：在 DEFAULT_DETAIL_FIELDS 基础上追加额外列，不替换默认列。
    # LLM 即使误设 extra_fields=["item_name"]，也只是追加（已在默认列中则无影响）。
    # 向后兼容：旧名 "fields" 映射到 extra_fields。
    raw_extra = params.get("extra_fields") or params.get("fields")
    if raw_extra:
        from services.kuaimai.erp_unified_schema import (
            COLUMN_WHITELIST, EXPORT_COLUMN_NAMES,
        )
        if isinstance(raw_extra, str):
            raw_extra = [raw_extra]
        valid = set(COLUMN_WHITELIST.keys()) | EXPORT_COLUMN_NAMES
        validated = [f for f in raw_extra if f in valid]
        if validated:
            clean["extra_fields"] = validated

    # ── 内部元数据透传（_ 前缀字段，计划模式用） ──
    for key, value in params.items():
        if key.startswith("_") and value is not None:
            clean[key] = value

    # ── 简单参数透传（str/bool，下游按需读取） ──
    # 空字符串/空列表跳过，防止产生无效过滤条件
    for key, value in params.items():
        if key in _COMPLEX_KEYS or key in clean:
            continue
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str) and value:
            clean[key] = value
        elif isinstance(value, list) and value:
            if key in _LIST_DICT_PARAMS and all(isinstance(v, dict) for v in value):
                clean[key] = value
            elif all(isinstance(v, str) for v in value):
                clean[key] = value

    return clean


_DOMAIN_TIME_COL: dict[str, str] = {
    "trade": "pay_time",
}


def _build_fallback_params(
    query: str,
    request_ctx: Any = None,
    domain: str = "",
) -> dict:
    """降级路径的最小参数构造（不用 LLM，纯规则）。"""
    params: dict = {"mode": "summary"}
    if request_ctx:
        today = request_ctx.now.strftime("%Y-%m-%d")
    else:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
    params["time_range"] = f"{today} ~ {today}"
    params["time_col"] = _DOMAIN_TIME_COL.get(domain, "doc_created_at")
    if any(kw in query for kw in ("导出", "Excel", "表格文件", "明细", "列表", "详情")):
        params["mode"] = "export"
    params["_degraded"] = True
    return params


# ── 参数定义文本（单域/多域 prompt 共用）──

_PARAM_DEFINITIONS = (
    "参数定义：\n"
    "【基础参数（必填）】\n"
    "- doc_type: order/purchase/purchase_return/aftersale/receipt/shelf/"
    "stock/product/sku/daily_stats/platform_map/batch_stock/order_log/aftersale_log（必填）\n"
    "  新表说明：stock=实时库存快照 / product=商品主数据 / sku=SKU明细 / "
    "daily_stats=商品日统计 / platform_map=平台商品映射 / "
    "batch_stock=批次效期库存 / order_log=订单操作日志 / aftersale_log=售后操作日志\n"
    "- mode: summary（统计汇总/多少/占比）/ export（获取数据/明细/导出/列表）（必填）\n"
    "- time_range: 标准化为 YYYY-MM-DD ~ YYYY-MM-DD 或 YYYY-MM-DD HH:MM ~ YYYY-MM-DD HH:MM（必填，根据当前时间推算；用户指定了具体时间点时带上 HH:MM）\n"
    "- time_col: pay_time（付款时间）/ consign_time（发货时间）/ doc_created_at（创建时间，默认）/ apply_date（售后申请日期）/ delivery_date（采购预计到货日）/ finished_at（售后完结日期）\n"
    "\n"
    "【通用过滤参数（可选，用户提到才提取）】\n"
    "- platform: taobao/pdd/douyin/jd/kuaishou/xhs/1688\n"
    "- product_code: 商品编码\n"
    "- order_no: 订单号（平台订单号或ERP系统单号）\n"
    "- shop_name: 店铺名称（模糊匹配）\n"
    "- warehouse_name: 仓库名称（模糊匹配）\n"
    "- item_name: 商品名称（模糊匹配）\n"
    "- creator_name: 创建人姓名（模糊匹配）\n"
    "- doc_code: 单据编号（精确匹配，如采购单号PO...、售后单号AS...）\n"
    "- sku_code: SKU编码/变体编码（精确匹配）\n"
    "\n"
    "【订单域过滤参数（doc_type=order 时可用）】\n"
    "- express_no: 快递单号（如SF/YT/ZTO/JD/EMS开头的单号）\n"
    "- express_company: 快递公司名（如顺丰/圆通/中通/韵达）\n"
    "- buyer_nick: 买家昵称（精确匹配）\n"
    "- receiver_name: 收件人姓名（精确匹配）\n"
    "- receiver_state: 收件省份（如广东/浙江/上海）\n"
    "- receiver_city: 收件城市（如深圳/杭州）\n"
    "- receiver_district: 收件区县（如朝阳区/余杭区）\n"
    "- receiver_address: 收件详细地址关键词（模糊匹配）\n"
    "- order_status: 订单状态。可选值: WAIT_BUYER_PAY(待付款)/WAIT_AUDIT(待审核)/"
    "WAIT_SEND_GOODS(待发货)/SELLER_SEND_GOODS(已发货)/FINISHED(已完成)/CLOSED(已关闭)\n"
    "- order_type: 订单类型。可选值: 补发/换货/预售/合并/拆分/加急\n"
    "- is_cancel: 布尔值，查已取消订单时设为 true\n"
    "- is_refund: 布尔值，查有退款的订单时设为 true\n"
    "- is_exception: 布尔值，查异常订单时设为 true\n"
    "- is_halt: 布尔值，查被拦截的订单时设为 true\n"
    "- is_urgent: 布尔值，查加急订单时设为 true\n"
    "- is_presell: 布尔值，查预售订单时设为 true\n"
    "- sku_properties_name: SKU规格属性关键词（如颜色/尺码/款式，模糊匹配）\n"
    "\n"
    "【售后域过滤参数（doc_type=aftersale 时可用）】\n"
    "- aftersale_type: 售后类型（如 仅退款/退货退款/换货）\n"
    "- refund_status: 退款状态（如 退款中/退款成功/退款关闭）\n"
    "- good_status: 货物状态（如 买家未发/买家已发/卖家已收/无需退货）\n"
    "- online_status: 售后在线状态（如 待卖家同意/待买家退货/退款成功/退款关闭/换货成功）\n"
    "- handler_status: 售后处理状态（如 待处理/处理成功/处理失败）\n"
    "- text_reason: 退货原因关键词（模糊匹配）\n"
    "- refund_express_no: 退货快递单号（精确匹配）\n"
    "- refund_express_company: 退货快递公司（模糊匹配）\n"
    "- refund_warehouse_name: 退货仓库（模糊匹配）\n"
    "- platform_refund_id: 平台退款单号（精确匹配）\n"
    "- reason: 退货原因编码（模糊匹配，用于按原因分类筛选）\n"
    "\n"
    "【采购域过滤参数（doc_type=purchase/purchase_return 时可用）】\n"
    "- supplier_name: 供应商名称（模糊匹配）\n"
    "- purchase_order_code: 采购单号（精确匹配）\n"
    "- doc_status: 单据状态（如 待审核/已审核/待收货/已完成）\n"
    "\n"
    "【新表专用过滤参数】\n"
    "- system_id: 订单系统ID（doc_type=order_log 时，精确匹配）\n"
    "- work_order_id: 售后工单号（doc_type=aftersale_log 时，精确匹配）\n"
    "- batch_no: 批次号（doc_type=batch_stock 时，精确匹配）\n"
    "- num_iid: 平台商品ID（doc_type=platform_map 时，精确匹配）\n"
    "\n"
    "【刷单/特殊过滤】\n"
    "- include_invalid: 布尔值，默认 false。仅当用户明确要求'包含全部'或'不排除刷单'时设为 true。\n"
    "- is_scalping: 布尔值，默认 false。用户查'刷单''空包'时设为 true。\n"
    "\n"
    "【数值过滤（可选，用户提到数量/金额/重量等数值条件时提取）】\n"
    "- numeric_filters: 数值条件数组，格式 [{\"field\":\"字段名\",\"op\":\"操作符\",\"value\":数值}]\n"
    "  field 可选: quantity(数量) / amount(金额) / price(单价) / cost(成本) / weight(重量) / "
    "pay_amount(实付) / gross_profit(毛利) / refund_money(退款额) / post_fee(运费)\n"
    "  op 可选: gt(大于) / gte(>=) / lt(<) / lte(<=) / between(区间)\n"
    "  value: 数字；between 时为 [min, max]\n"
    "  关键词映射：不足/低于/少于/小于/以下 → lt；超过/多于/大于/以上 → gt；之间/到 → between\n"
    "\n"
    "【否定/排除过滤（可选，用户说'不是/非/除了/排除'时提取）】\n"
    "- exclude_filters: 排除条件数组，格式 [{\"field\":\"字段名\",\"value\":\"排除值\"}]\n"
    "  单值: [{\"field\":\"platform\",\"value\":\"taobao\"}] → platform != taobao\n"
    "  多值: [{\"field\":\"platform\",\"value\":[\"taobao\",\"pdd\"]}] → platform NOT IN (taobao, pdd)\n"
    "\n"
    "【空值检查（可选，用户说'没有/为空/缺少/未填'时提取）】\n"
    "- null_fields: 要筛选为空的字段名列表，如 [\"express_no\"]\n"
    "\n"
    "【展示控制】\n"
    "- group_by: shop/platform/product/supplier/warehouse/status（可选，仅 summary 模式）\n"
    "- sort_by: 排序字段（如 quantity/amount，默认按时间降序）\n"
    "- sort_dir: asc(升序) / desc(降序，默认)\n"
    "- limit: 返回条数上限（\"前10名\"→limit:10，默认20）\n"
    "- extra_fields: 在默认列基础上追加的额外列（可选，绝大多数查询不需要设置）\n"
    "  默认已返回：单据编号/商品编码/商品名称/数量/金额/状态/时间等核心列\n"
    "  仅当用户明确要求看以下额外信息时才设置：\n"
    "  remark(备注)/buyer_message(买家留言)/express_no(快递单号)/"
    "express_company(快递公司)/buyer_nick(买家昵称)/receiver_name(收件人)/"
    "receiver_address(地址)/cost(成本)/gross_profit(毛利)/text_reason(退货原因)\n\n"
    "【重要规则】\n"
    "- 参数相关性原则：只提取用户查询中作为筛选条件的参数。"
    "查询文本中出现的统计指标名称（如'刷单数''取消订单数''有效金额'）是期望的输出，不是过滤条件，不要提取为参数。"
    "判断标准：去掉这个参数后查询意图是否改变？不改变就不提取。\n"
    "- 用户给了一个单号但没说是什么类型时：纯数字16-19位→order_no；字母+数字（如SF/YT/ZTO/JD开头）→express_no\n"
    "- 用户指定订单号或快递单号查询时，time_range 仍然必填（用最近3个月）\n"
    "- 用户未指定具体状态值时不要猜测，留空让系统返回全部\n"
)


# ── LLM Prompt（单域扁平结构）──

def build_extract_prompt(query: str, now_str: str = "") -> str:
    """构建让 LLM 提取单域查询参数的 prompt。

    输出格式：{"domain": "trade", "params": {...}}
    """
    time_line = f"当前时间：{now_str}\n\n" if now_str else ""
    return (
        f"{time_line}"
        "分析以下用户查询，提取查询域和参数（JSON格式）。\n\n"
        f"用户查询：{query}\n\n"
        "可用域：\n"
        "- warehouse：库存/仓库/出入库/盘点\n"
        "- purchase：采购/供应商/到货/采退\n"
        "- trade：订单/物流/发货\n"
        "- aftersale：退货/退款/售后\n\n"
        "规则：\n"
        "1. 只输出一个域（每次查询只查一个域的数据）\n"
        "2. 如果查询涉及多个域，选最主要的那个\n\n"
        + _PARAM_DEFINITIONS +
        "\n返回纯 JSON（不要 markdown 围栏）。\n\n"
        "示例1（今日付款订单统计）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-17 ~ 2026-04-17","time_col":"pay_time"}}\n\n'
        "示例2（查快递单号对应订单）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"export",'
        '"time_range":"2026-01-21 ~ 2026-04-21","express_no":"SF1234567890"}}\n\n'
        "示例3（XX旗舰店待发货订单）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-17 ~ 2026-04-17","shop_name":"XX旗舰店",'
        '"order_status":"WAIT_SEND_GOODS"}}\n\n'
        "示例4（退货按商品分组统计）：\n"
        '{"domain": "aftersale", "params": {"doc_type":"aftersale","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17","group_by":"product"}}\n\n'
        "示例5（XX供应商的采购单）：\n"
        '{"domain": "purchase", "params": {"doc_type":"purchase","mode":"export",'
        '"time_range":"2026-04-01 ~ 2026-04-17","supplier_name":"XX供应商"}}\n\n'
        "示例6（刷单统计）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17","is_scalping":true,"include_invalid":true}}\n\n'
        "示例7（买家张三的订单）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"export",'
        '"time_range":"2026-01-21 ~ 2026-04-21","buyer_nick":"张三"}}\n\n'
        "示例8（因质量问题的退货）：\n"
        '{"domain": "aftersale", "params": {"doc_type":"aftersale","mode":"export",'
        '"time_range":"2026-04-01 ~ 2026-04-17","text_reason":"质量"}}\n\n'
        "示例9（库存不足10件的商品）：\n"
        '{"domain": "warehouse", "params": {"doc_type":"shelf","mode":"export",'
        '"time_range":"2026-04-17 ~ 2026-04-17",'
        '"numeric_filters":[{"field":"quantity","op":"lt","value":10}]}}\n\n'
        "示例10（金额最高的10笔订单）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"export",'
        '"time_range":"2026-04-17 ~ 2026-04-17",'
        '"sort_by":"amount","sort_dir":"desc","limit":10}}\n\n'
        "示例11（除了淘宝平台的订单）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-17 ~ 2026-04-17",'
        '"exclude_filters":[{"field":"platform","value":"taobao"}]}}\n\n'
        "示例12（没有快递单号的已发货订单）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"export",'
        '"time_range":"2026-04-17 ~ 2026-04-17",'
        '"order_status":"SELLER_SEND_GOODS","null_fields":["express_no"]}}\n\n'
        "示例13（库存负数的商品有多少）：\n"
        '{"domain": "warehouse", "params": {"doc_type":"stock","mode":"summary",'
        '"numeric_filters":[{"field":"available_stock","op":"lt","value":0}]}}\n\n'
        "示例14（停售商品列表）：\n"
        '{"domain": "warehouse", "params": {"doc_type":"product","mode":"export",'
        '"numeric_filters":[{"field":"active_status","op":"eq","value":2}]}}\n\n'
        "示例15（本月各商品销量Top10）：\n"
        '{"domain": "warehouse", "params": {"doc_type":"daily_stats","mode":"export",'
        '"time_range":"2026-04-01 ~ 2026-04-26",'
        '"sort_by":"order_qty","sort_dir":"desc","limit":10}}\n\n'
        "示例16（某商品在哪些平台售卖）：\n"
        '{"domain": "warehouse", "params": {"doc_type":"platform_map","mode":"export",'
        '"product_code":"HZ001"}}\n\n'
        "示例17（某订单的操作记录）：\n"
        '{"domain": "trade", "params": {"doc_type":"order_log","mode":"export",'
        '"time_range":"2026-01-01 ~ 2026-04-26",'
        '"system_id":"123456"}}\n\n'
        "示例18（快过期的批次库存）：\n"
        '{"domain": "warehouse", "params": {"doc_type":"batch_stock","mode":"export"}}'
    )


# ── LLM Prompt（多域编排结构）──


def build_multi_extract_prompt(query: str, now_str: str = "") -> str:
    """构建让 LLM 提取多域查询计划的 prompt。

    输出格式：{"steps": [{"domain":"...", "params":{...}}, ...], "compute_hint":"..."}
    单域查询输出 1 个 step（最常见场景），跨域输出 2-4 个 step。
    """
    time_line = f"当前时间：{now_str}\n\n" if now_str else ""
    return (
        f"{time_line}"
        "分析以下用户查询，提取查询计划（JSON格式）。\n\n"
        f"用户查询：{query}\n\n"
        "可用域：\n"
        "- warehouse：库存/仓库/出入库/盘点\n"
        "- purchase：采购/供应商/到货/采退\n"
        "- trade：订单/物流/发货\n"
        "- aftersale：退货/退款/售后\n\n"
        "规则：\n"
        "1. 大部分查询只涉及一个域 → 输出 1 个 step\n"
        "2. 仅当用户需要跨域关联数据时输出多个 step（最多4个）\n"
        "   跨域场景：退货率（订单+售后）、商品流转（订单+采购+库存）、"
        "采购到货与销售对比（采购+订单）\n"
        "3. 每个 step 独立提取参数，共享相同的时间范围和过滤条件\n"
        "4. compute_hint 仅在跨域需要计算时填写，"
        "告诉下游怎么关联和计算（用哪个字段 join、算什么指标）\n"
        "5. 不确定是否跨域时，默认单域\n"
        "6. 多 step 时补充以下字段：\n"
        "   a. dependency（必填）：\n"
        '      - "parallel"（默认）：各 step 过滤条件互相独立，可同时执行\n'
        '      - "serial"：后续 step 需要前序 step 的查询结果作为过滤条件\n'
        "      判断标准：后续 step 的某个过滤参数在用户查询中没给明确值，"
        "需要从前序 step 结果获取 → serial\n"
        "   b. 每个 step 的 params 中补充（serial 时必填）：\n"
        "      - _expected_output：该步骤预期产出什么数据给后续步骤\n"
        "      - _dependencies：依赖哪些前序步骤（步骤序号数组，从1开始）\n"
        "      - _required_input：需要前序步骤的什么字段"
        "（如 {\"from_step\":1,\"field\":\"product_code\"}）\n\n"
        + _PARAM_DEFINITIONS +
        "\n返回纯 JSON（不要 markdown 围栏）。\n\n"
        "示例1（单域：今日付款订单统计）：\n"
        '{"steps":[{"domain":"trade","params":{"doc_type":"order",'
        '"mode":"summary","time_range":"2026-04-17 ~ 2026-04-17",'
        '"time_col":"pay_time"}}]}\n\n'
        "示例2（单域：退货按商品分组）：\n"
        '{"steps":[{"domain":"aftersale","params":{"doc_type":"aftersale",'
        '"mode":"summary","time_range":"2026-04-01 ~ 2026-04-17",'
        '"group_by":"product"}}]}\n\n'
        "示例3（跨域 parallel：HZ001 商品的退货率）：\n"
        '{"steps":['
        '{"domain":"trade","params":{"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17","product_code":"HZ001"}},'
        '{"domain":"aftersale","params":{"doc_type":"aftersale",'
        '"mode":"summary","time_range":"2026-04-01 ~ 2026-04-17",'
        '"product_code":"HZ001"}}'
        '],"compute_hint":"用 product_code 关联，'
        '退货率 = 售后笔数 / 订单笔数","dependency":"parallel"}\n\n'
        "示例4（跨域 parallel：本月各商品采购到货与销量对比）：\n"
        '{"steps":['
        '{"domain":"purchase","params":{"doc_type":"purchase",'
        '"mode":"summary","time_range":"2026-04-01 ~ 2026-04-17",'
        '"group_by":"product"}},'
        '{"domain":"trade","params":{"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17","group_by":"product"}}'
        '],"compute_hint":"用 product_code 关联采购量和销量，'
        '计算采销比","dependency":"parallel"}\n\n'
        "示例5（单域：刷单统计）：\n"
        '{"steps":[{"domain":"trade","params":{"doc_type":"order",'
        '"mode":"summary","time_range":"2026-04-01 ~ 2026-04-17",'
        '"is_scalping":true,"include_invalid":true}}]}\n\n'
        "示例6（跨域 serial：查供应商采购商品→用编码查订单）：\n"
        '{"steps":['
        '{"domain":"purchase","params":{"doc_type":"purchase","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17","supplier_name":"XX",'
        '"group_by":"product",'
        '"_expected_output":"商品编码列表（product_code）","_dependencies":[]}},'
        '{"domain":"trade","params":{"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17",'
        '"_expected_output":"订单数据","_dependencies":[1],'
        '"_required_input":{"from_step":1,"field":"product_code"}}}'
        '],"compute_hint":"先查供应商采购商品获取编码，再用编码查订单",'
        '"dependency":"serial"}\n\n'
        "示例7（单域：库存不足10件的商品按数量排序）：\n"
        '{"steps":[{"domain":"warehouse","params":{"doc_type":"shelf",'
        '"mode":"export","time_range":"2026-04-17 ~ 2026-04-17",'
        '"numeric_filters":[{"field":"quantity","op":"lt","value":10}],'
        '"sort_by":"quantity","sort_dir":"asc","limit":50}}]}\n\n'
        "示例8（单域：除了淘宝和拼多多的订单）：\n"
        '{"steps":[{"domain":"trade","params":{"doc_type":"order",'
        '"mode":"summary","time_range":"2026-04-17 ~ 2026-04-17",'
        '"exclude_filters":[{"field":"platform","value":["taobao","pdd"]}]}}]}\n\n'
        "示例9（单域：没有快递单号的已发货订单）：\n"
        '{"steps":[{"domain":"trade","params":{"doc_type":"order",'
        '"mode":"export","time_range":"2026-04-17 ~ 2026-04-17",'
        '"order_status":"SELLER_SEND_GOODS","null_fields":["express_no"]}}]}\n\n'
        "示例10（单域：金额最高的10笔订单）：\n"
        '{"steps":[{"domain":"trade","params":{"doc_type":"order",'
        '"mode":"export","time_range":"2026-04-17 ~ 2026-04-17",'
        '"sort_by":"amount","sort_dir":"desc","limit":10}}]}'
    )


def parse_multi_extract_response(
    raw_json: str,
) -> tuple[list[tuple[str, dict]], str | None, str]:
    """解析 LLM 返回的多域计划 JSON。

    返回 (steps: [(domain, params), ...], compute_hint: str | None, dependency: str)。
    dependency: "parallel"（默认）或 "serial"。
    向后兼容：旧格式 {"domain":..., "params":...} 自动包装为单 step。
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_json)
    cleaned = cleaned.replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回的不是合法 JSON: {e}")

    if not isinstance(data, dict):
        raise ValueError("LLM 返回格式不是 dict")

    # ── 向后兼容：旧单域格式 {"domain":..., "params":...} ──
    if "domain" in data and "steps" not in data:
        domain = data["domain"]
        if domain not in VALID_DOMAINS:
            raise ValueError(
                f"未知域 '{domain}'，可选: {', '.join(sorted(VALID_DOMAINS))}",
            )
        params = data.get("params", {})
        if not isinstance(params, dict):
            params = {}
        return ([(domain, params)], None, "parallel")

    # ── 新多域格式 {"steps":[...], "compute_hint":"..."} ──
    steps_raw = data.get("steps")
    if not steps_raw or not isinstance(steps_raw, list):
        raise ValueError("LLM 返回缺少 steps 数组")

    steps: list[tuple[str, dict]] = []
    for i, step in enumerate(steps_raw):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{i}] 不是 dict")
        domain = step.get("domain", "")
        if not domain:
            raise ValueError(f"steps[{i}] 缺少 domain")
        if domain not in VALID_DOMAINS:
            raise ValueError(
                f"steps[{i}] 未知域 '{domain}'，"
                f"可选: {', '.join(sorted(VALID_DOMAINS))}",
            )
        params = step.get("params", {})
        if not isinstance(params, dict):
            params = {}
        steps.append((domain, params))

    if not steps:
        raise ValueError("steps 数组为空")
    if len(steps) > 4:
        logger.warning(f"LLM 返回 {len(steps)} 个 step，截断到 4 个")
        steps = steps[:4]

    compute_hint = data.get("compute_hint")
    if compute_hint and not isinstance(compute_hint, str):
        compute_hint = None

    dependency = data.get("dependency", "parallel")
    if dependency not in ("parallel", "serial"):
        dependency = "parallel"

    # 自动纠正：任一 step 含 _required_input 但顶层 dependency 不是 serial
    if dependency != "serial":
        for _, params in steps:
            if params.get("_required_input"):
                logger.warning(
                    "dependency 自动纠正: step 含 _required_input 但 "
                    f"dependency={dependency!r} → serial"
                )
                dependency = "serial"
                break

    return (steps, compute_hint, dependency)


def parse_extract_response(raw_json: str) -> tuple[str, dict]:
    """解析 LLM 返回的 JSON 为 (domain, params)。

    容错：去除 markdown 围栏，校验域名合法性。
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_json)
    cleaned = cleaned.replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回的不是合法 JSON: {e}")

    if not isinstance(data, dict):
        raise ValueError("LLM 返回格式不是 dict")

    domain = data.get("domain", "")
    if not domain:
        raise ValueError("LLM 返回缺少 domain 字段")
    if domain not in VALID_DOMAINS:
        raise ValueError(
            f"未知域 '{domain}'，可选: {', '.join(sorted(VALID_DOMAINS))}",
        )

    params = data.get("params", {})
    if not isinstance(params, dict):
        params = {}

    return (domain, params)


# ── 能力清单（已拆到 erp_tool_description.py，此处 re-export 保持兼容） ──
from services.agent.erp_tool_description import get_capability_manifest  # noqa: F401

# ── L2 补全（已拆到 plan_fill.py，此处 re-export 保持兼容） ──
from services.agent.plan_fill import (  # noqa: F401
    fill_platform,
    _fill_codes_for_params,
    _PRODUCT_CODE_RE,
    _ORDER_NO_RE,
    _EXPRESS_NO_RE,
    _CODE_STOP_WORDS,
)


# ── 向后兼容保留（测试文件引用）──
# 以下保留旧接口，供未迁移的测试暂时使用，Phase 4 删除

def build_plan_prompt(query: str, now_str: str = "") -> str:
    """向后兼容：转发到 build_extract_prompt。"""
    return build_extract_prompt(query, now_str=now_str)
