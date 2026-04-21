"""
ERP 查询参数提取工具函数。

提供 ERPAgent 需要的：
- 关键词路由（quick_classify）
- 参数校验（_sanitize_params）
- 平台/编码补全（_fill_platform, _fill_codes_for_params）
- LLM prompt 构建与解析（build_extract_prompt, parse_extract_response）
- 降级参数构造（_build_fallback_params）

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
    "trade": frozenset({"order"}),
    "purchase": frozenset({"purchase", "purchase_return"}),
    "aftersale": frozenset({"aftersale"}),
    "warehouse": frozenset({"receipt", "shelf"}),
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
})
# 向后兼容旧名
_VALID_MODES = VALID_MODES
_VALID_DOC_TYPES = VALID_DOC_TYPES
_TIME_RANGE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?\s*~\s*\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?$",
)
# _sanitize_params 中已做特殊校验/变换的参数，透传逻辑跳过这些 key
_COMPLEX_KEYS = frozenset({"mode", "doc_type", "time_range", "group_by", "fields"})


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

    # fields: 需要白名单校验
    if params.get("fields"):
        from services.kuaimai.erp_unified_schema import (
            COLUMN_WHITELIST, EXPORT_COLUMN_NAMES,
        )
        fields = params["fields"]
        if isinstance(fields, str):
            fields = [fields]
        valid = set(COLUMN_WHITELIST.keys()) | EXPORT_COLUMN_NAMES
        clean["fields"] = [f for f in fields if f in valid]
        if not clean["fields"]:
            del clean["fields"]

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
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
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
        "参数定义：\n"
        "- doc_type: order/purchase/purchase_return/aftersale/receipt/shelf（必填）\n"
        "- mode: summary（统计汇总/多少/占比）/ export（获取数据/明细/导出/列表）（必填）\n"
        "- time_range: 标准化为 YYYY-MM-DD ~ YYYY-MM-DD 或 YYYY-MM-DD HH:MM ~ YYYY-MM-DD HH:MM（必填，根据当前时间推算；用户指定了具体时间点时带上 HH:MM）\n"
        "- time_col: pay_time（付款时间）/ consign_time（发货时间）/ doc_created_at（创建时间，默认）\n"
        "- platform: taobao/pdd/douyin/jd/kuaishou/xhs/1688（可选）\n"
        "- group_by: shop/platform/product/supplier/warehouse/status（可选，仅 summary 模式）\n"
        "- product_code: 商品编码（如用户提到了具体编码则提取）\n"
        "- order_no: 订单号（如用户提到了则提取）\n"
        "- include_invalid: 布尔值，默认 false。仅当用户明确要求'包含全部'或'不排除刷单'时设为 true。\n"
        "- is_scalping: 布尔值，默认 false。用户查'刷单''空包'时设为 true（筛选 is_scalping=1 的订单）。\n"
        "- fields: 需要返回的特定字段列表（可选，用户明确提到特定信息时提取）\n"
        "  可选字段：remark(备注)/buyer_message(买家留言)/express_no(快递单号)/"
        "express_company(快递公司)/buyer_nick(买家昵称)/receiver_name(收件人)/"
        "receiver_address(地址)/cost(成本)/gross_profit(毛利)/text_reason(退货原因)\n"
        "  注意：不提则用默认字段，不要主动添加用户未提到的字段\n\n"
        "返回纯 JSON（不要 markdown 围栏）。\n\n"
        "示例1（今日付款订单统计）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-17 ~ 2026-04-17","time_col":"pay_time"}}\n\n'
        "示例2（昨天淘宝订单统计）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-16 ~ 2026-04-16","time_col":"pay_time",'
        '"platform":"taobao"}}\n\n'
        "示例3（退货按商品分组统计）：\n"
        '{"domain": "aftersale", "params": {"doc_type":"aftersale","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17","group_by":"product"}}\n\n'
        "示例4（采购单含备注）：\n"
        '{"domain": "purchase", "params": {"doc_type":"purchase","mode":"export",'
        '"time_range":"2026-04-01 ~ 2026-04-17","fields":["remark","doc_code","supplier_name"]}}\n\n'
        "示例5（刷单统计）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-01 ~ 2026-04-17","is_scalping":true,"include_invalid":true}}\n\n'
        "示例6（导出订单到 Excel）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"export",'
        '"time_range":"2026-04-17 ~ 2026-04-17","time_col":"pay_time"}}\n\n'
        "示例7（导出刷单订单表格）：\n"
        '{"domain": "trade", "params": {"doc_type":"order","mode":"export",'
        '"time_range":"2026-04-17 ~ 2026-04-17","is_scalping":true,"include_invalid":true}}'
    )


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


# ── 能力清单导出（供 build_tool_description 消费） ──


def get_capability_manifest() -> dict:
    """导出 erp_agent 完整能力清单（唯一 Source of Truth）。

    所有内容结构化，build_tool_description() 纯格式化消费。
    设计文档: docs/document/TECH_Agent能力通信架构.md §3.3.1
    """
    from services.kuaimai.erp_unified_schema import (
        GROUP_BY_MAP, VALID_TIME_COLS, PLATFORM_NORMALIZE,
        EXPORT_COLUMNS,
    )
    group_by_dims = sorted({v for v in GROUP_BY_MAP.values()})
    platform_names = sorted({
        k for k in PLATFORM_NORMALIZE if not k.isascii()
    })
    field_categories = {
        category: [cn_name for _, cn_name in fields]
        for category, fields in EXPORT_COLUMNS.items()
    }

    return {
        "domains": sorted(VALID_DOMAINS),
        "modes": sorted(VALID_MODES),
        "doc_types": sorted(VALID_DOC_TYPES),
        "group_by": group_by_dims,
        "filters": ["platform", "product_code", "order_no", "include_invalid"],
        "time_cols": sorted(VALID_TIME_COLS),
        "platforms": platform_names,
        "field_categories": field_categories,
        "summary": (
            "ERP 数据查询专员，查询订单/库存/采购/售后等数据，"
            "口语化表达和错别字自动识别"
        ),
        "use_when": [
            "用户问任何涉及订单/库存/采购/售后/发货/物流/商品/销量的问题",
            "含操作性词汇（对账/核对/处理/优先处理/多少钱/价格）需要先查数据",
            ("口语/错别字也要识别：'丁单'=订单，'酷存'=库存，"
             "'够不够卖'=库存查询，'到了没'=采购到货，"
             "'退了'=售后，'爆单'=销量统计，'查一下呗'=数据查询"),
        ],
        "dont_use_when": [
            {"场景": "写操作（创建/修改/取消）", "替代": "erp_execute"},
            {"场景": "非 ERP 数据（天气/新闻）", "替代": "web_search"},
            {"场景": "业务规则/操作流程", "替代": "search_knowledge"},
        ],
        "returns": [
            "summary 模式：统计数字（总量/金额/分组明细），直接内联",
            "export 模式：数据存 staging parquet + 返回 profile 摘要（行数/字段/预览）",
            "大数据导出工作流：erp_agent 查数据存 staging → code_execute 读 staging 写 Excel",
            "每次只查一个业务域，跨域数据并行调用多次",
        ],
        "examples": [
            {"query": "昨天淘宝退货按店铺统计",
             "effect": "summary + platform=taobao + group_by=shop"},
            {"query": "导出本周订单明细", "effect": "export → staging + profile"},
            {"query": "编码 HZ001 的库存", "effect": "product_code 过滤"},
            {"query": "上月采购到货按供应商统计",
             "effect": "summary + group_by=supplier"},
            {"query": "包含刷单的订单有多少",
             "effect": "include_invalid=true"},
            {"query": "今天刷单有多少",
             "effect": "is_scalping=true + include_invalid=true"},
        ],
        "auto_behaviors": [
            ">200行自动导出 staging 文件",
            "返回格式自动适配（文本/表格/文件链接）",
            "降级链：AI提取 → 关键词匹配 → abort",
        ],
    }


# ── L2 product_code / order_no 补全（DB 验证） ──

_PRODUCT_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_ORDER_NO_RE = re.compile(r"P\d{18}|\d{16,19}")
_CODE_STOP_WORDS = frozenset({
    "the", "and", "for", "not", "all", "but", "are", "was",
    "order", "trade", "shop", "sku", "erp",
})


async def _fill_codes_for_params(
    params: dict, query: str, db: Any, org_id: str | None,
) -> None:
    """L2 意图完整性：从用户查询文本补全 product_code / order_no。

    与旧版 _fill_codes 功能一致，但操作单个 params dict 而非 ExecutionPlan。
    """
    if not db:
        return

    code_candidates = _PRODUCT_CODE_RE.findall(query)
    code_candidates = [
        c for c in code_candidates
        if len(c) >= 3 and c.lower() not in _CODE_STOP_WORDS
    ][:5]
    verified_code: str | None = None
    if code_candidates:
        verified_code = await _verify_product_code(db, code_candidates, org_id)

    order_candidates = _ORDER_NO_RE.findall(query)[:3]
    verified_order: str | None = None
    if order_candidates:
        verified_order = await _verify_order_no(db, order_candidates, org_id)

    if not verified_code and not verified_order:
        return

    if verified_code and not params.get("product_code"):
        params["product_code"] = verified_code
        logger.info(
            f"L2 product_code 补全: query={query!r} → "
            f"product_code={verified_code}",
        )
    if verified_order and not params.get("order_no"):
        params["order_no"] = verified_order
        logger.info(
            f"L2 order_no 补全: query={query!r} → "
            f"order_no={verified_order}",
        )


async def _verify_product_code(
    db: Any, candidates: list[str], org_id: str | None,
) -> str | None:
    """验证候选商品编码是否存在于 erp_products 表。"""
    matched: set[str] = set()
    for code in candidates:
        try:
            q = db.table("erp_products").select("outer_id").eq(
                "outer_id", code,
            ).limit(1)
            if org_id:
                q = q.eq("org_id", org_id)
            result = q.execute()
            if result.data:
                matched.add(code)
        except Exception as e:
            logger.debug(f"L2 product_code 验证失败: {code} → {e}")
    if len(matched) == 1:
        return matched.pop()
    if len(matched) > 1:
        logger.warning(f"L2 product_code 多匹配，不补全: {matched}")
    return None


async def _verify_order_no(
    db: Any, candidates: list[str], org_id: str | None,
) -> str | None:
    """验证候选订单号是否存在于 erp_document_items 表。"""
    matched: set[str] = set()
    for order_no in candidates:
        try:
            q = db.table("erp_document_items").select("order_no").eq(
                "order_no", order_no,
            ).limit(1)
            if org_id:
                q = q.eq("org_id", org_id)
            result = q.execute()
            if result.data:
                matched.add(order_no)
        except Exception as e:
            logger.debug(f"L2 order_no 验证失败: {order_no} → {e}")
    if len(matched) == 1:
        return matched.pop()
    if len(matched) > 1:
        logger.warning(f"L2 order_no 多匹配，不补全: {matched}")
    return None


# ── 向后兼容保留（测试文件引用）──
# 以下保留旧接口，供未迁移的测试暂时使用，Phase 4 删除

def build_plan_prompt(query: str, now_str: str = "") -> str:
    """向后兼容：转发到 build_extract_prompt。"""
    return build_extract_prompt(query, now_str=now_str)
