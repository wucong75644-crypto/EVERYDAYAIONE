"""L2 意图完整性补全 — platform / product_code / order_no / express_no。

从 plan_builder.py 拆出，减少主文件行数。
ERPAgent._extract_plan 通过 fill_platform / _fill_codes_for_params 调用。

设计文档: docs/document/TECH_意图完整性校验层.md
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger


# ── L2 platform 自动补全 ──


def _llm_handled_platform(params: dict) -> bool:
    """主防线：检查 LLM 是否已在任何形式中对 platform 做出了决策。

    包括：正向过滤(platform=)、反向排除(exclude_filters)、
    已转换的 filters DSL。只要 LLM 碰过 platform，fill_platform 就不该干预。
    """
    if params.get("platform"):
        return True
    for ef in params.get("exclude_filters", []):
        if ef.get("field") == "platform":
            return True
    for f in params.get("filters", []):
        if isinstance(f, dict) and f.get("field") == "platform":
            return True
    return False


def _is_negated_in_text(query: str, keyword: str) -> bool:
    """安全网：检查 query 中 keyword 附近是否有否定语义。

    用于 LLM 失灵（漏提取 exclude_filters）时的文本级兜底。
    前置："非淘宝""除了京东""不是拼多多""排除淘宝""不含抖音"
    后置："淘宝之外""淘宝以外"
    """
    idx = query.find(keyword)
    if idx < 0:
        return False
    prefix = query[max(0, idx - 4):idx]
    if any(neg in prefix for neg in ("不是", "除了", "排除", "不含")):
        return True
    if idx >= 1 and query[idx - 1] == "非":
        return True
    end = idx + len(keyword)
    suffix = query[end:end + 2]
    if suffix in ("之外", "以外"):
        return True
    return False


def fill_platform(params: dict, query: str) -> None:
    """L2 意图完整性：从用户查询文本补全 LLM 漏提取的 platform。

    纯函数，不依赖 ERPAgent 实例。供外部调用或测试使用。
    两层防御：
      1. 主防线 _llm_handled_platform — LLM 已处理 platform → 不干预
      2. 安全网 _is_negated_in_text — LLM 漏提取但文本有否定语义 → 不补全
    仅当两层都未触发时才做关键词兜底补全。
    """
    # 主防线：LLM 已处理
    if _llm_handled_platform(params):
        return

    from services.kuaimai.erp_unified_schema import PLATFORM_NORMALIZE
    cn_keys = [
        k for k in PLATFORM_NORMALIZE
        if not k.isascii() or k == "1688"
    ]
    matched: set[str] = set()
    for key in cn_keys:
        # 安全网：文本中有否定语义的平台名不算匹配
        if key in query and not _is_negated_in_text(query, key):
            matched.add(PLATFORM_NORMALIZE[key])

    if len(matched) == 1:
        params["platform"] = matched.pop()
        logger.info(
            f"L2 platform 补全: query={query!r} → "
            f"platform={params['platform']}",
        )
    elif len(matched) > 1:
        logger.warning(
            f"L2 platform 多匹配，不补全: query={query!r}, "
            f"matched={matched}",
        )


# ── L2 product_code / order_no / express_no 补全（DB 验证） ──

# 正则统一维护在 input_normalizer.ValueValidator（single source of truth）
# SEARCH_PATTERNS：搜索模式（无 ^$），从自然语言文本中提取候选值
from services.agent.input_normalizer import ValueValidator as _VV

_PRODUCT_CODE_RE = _VV.SEARCH_PATTERNS["product_code"]
_ORDER_NO_RE = _VV.SEARCH_PATTERNS["order_no"]
_EXPRESS_NO_RE = _VV.SEARCH_PATTERNS["express_no"]
_CODE_STOP_WORDS = frozenset({
    "the", "and", "for", "not", "all", "but", "are", "was",
    "order", "trade", "shop", "sku", "erp",
})


async def _fill_codes_for_params(
    params: dict, query: str, db: Any, org_id: str | None,
) -> None:
    """L2 意图完整性：从用户查询文本补全 product_code / order_no / express_no。

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

    # 快递单号识别（字母前缀+数字，如 SF1234567890）
    express_candidates = _EXPRESS_NO_RE.findall(query)[:3]
    verified_express: str | None = None
    if express_candidates:
        verified_express = await _verify_express_no(
            db, express_candidates, org_id,
        )

    if not verified_code and not verified_order and not verified_express:
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
    if verified_express and not params.get("express_no"):
        params["express_no"] = verified_express
        logger.info(
            f"L2 express_no 补全: query={query!r} → "
            f"express_no={verified_express}",
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


async def _verify_express_no(
    db: Any, candidates: list[str], org_id: str | None,
) -> str | None:
    """验证候选快递单号是否存在于 erp_document_items 表。"""
    matched: set[str] = set()
    for express_no in candidates:
        try:
            q = db.table("erp_document_items").select("express_no").eq(
                "express_no", express_no,
            ).limit(1)
            if org_id:
                q = q.eq("org_id", org_id)
            result = q.execute()
            if result.data:
                matched.add(express_no)
        except Exception as e:
            logger.debug(f"L2 express_no 验证失败: {express_no} → {e}")
    if len(matched) == 1:
        return matched.pop()
    if len(matched) > 1:
        logger.warning(f"L2 express_no 多匹配，不补全: {matched}")
    return None


# ── L2 query_type / time_granularity / compare_range / metrics / alert_type 兜底 ──


_ALERT_KEYWORDS: dict[str, list[str]] = {
    "low_stock": ["缺货", "断货", "库存不足", "快没了", "补货"],
    "slow_moving": ["滞销", "卖不动", "零销量", "不动销"],
    "overstock": ["积压", "库存过多", "超库存"],
    "out_of_stock": ["售罄", "卖完了", "没库存"],
    "purchase_overdue": ["采购超期", "采购未到", "逾期未到货", "催货"],
}

_METRIC_KEYWORDS: dict[str, list[str]] = {
    "return_rate": ["退货率", "退货比例"],
    "refund_rate": ["退款率", "退款比例"],
    "aftersale_rate": ["售后率", "售后比例"],
    "avg_order_value": ["客单价", "均价", "平均订单金额"],
    "repurchase_rate": ["复购率", "回头客", "复购"],
    "gross_margin": ["毛利率", "毛利", "利润率"],
    "purchase_fulfillment": ["采购达成率", "到货率"],
    "supplier_evaluation": ["供应商评估", "供应商考核", "供应商退货率"],
    "inventory_turnover": ["库存周转", "周转天数", "周转率"],
    "sell_through_rate": ["动销率", "动销"],
    "inventory_flow": ["进销存", "进出存", "进货出货库存"],
    "avg_ship_time": ["发货时效", "发货时长", "平均发货", "发货速度"],
    "same_day_rate": ["当日发货率", "当天发货"],
}


def _fill_alert_type(params: dict, query: str) -> None:
    """从文本关键词推断 alert_type。"""
    for at, keywords in _ALERT_KEYWORDS.items():
        if any(kw in query for kw in keywords):
            params["alert_type"] = at
            return


def _fill_metric(params: dict, query: str) -> None:
    """从文本关键词推断 metrics。"""
    for metric, keywords in _METRIC_KEYWORDS.items():
        if any(kw in query for kw in keywords):
            params["metrics"] = [metric]
            return


def _fill_time_granularity(params: dict, query: str) -> None:
    """从文本关键词推断 time_granularity。"""
    for kw in ("每天", "日", "逐日", "按天"):
        if kw in query:
            params["time_granularity"] = "day"
            return
    for kw in ("每周", "周", "逐周", "按周"):
        if kw in query:
            params["time_granularity"] = "week"
            return
    for kw in ("每月", "月", "逐月", "按月", "月度"):
        if kw in query:
            params["time_granularity"] = "month"
            return


def _fill_compare_range(params: dict, query: str) -> None:
    """从文本关键词推断 compare_range。"""
    for kw in ("环比", "上个月", "上月", "月环比", "比上个月"):
        if kw in query:
            params["compare_range"] = "mom"
            return
    for kw in ("同比", "去年", "同期", "去年同月", "年同比"):
        if kw in query:
            params["compare_range"] = "yoy"
            return
    for kw in ("周环比", "上周", "比上周"):
        if kw in query:
            params["compare_range"] = "wow"
            return


def fill_query_type(params: dict, query: str) -> None:
    """L2 兜底：从文本关键词推断 query_type（LLM 未提取时）。

    优先级：alert > cross > trend > compare > ratio > distribution。
    同时联动补全关联参数（alert_type / metrics / time_granularity / compare_range）。
    """
    if params.get("query_type") and params["query_type"] != "auto":
        return  # LLM 已提取有效值，不覆盖

    # alert 关键词
    for keyword in ("预警", "断货", "缺货", "滞销", "快没了", "采购超期",
                    "积压", "售罄", "卖完了", "催货"):
        if keyword in query:
            params["query_type"] = "alert"
            if not params.get("alert_type"):
                _fill_alert_type(params, query)
            logger.info(f"L2 query_type 补全: alert (keyword={keyword!r})")
            return

    # cross 关键词
    for keyword in ("退货率", "毛利率", "客单价", "复购率", "周转", "进销存",
                    "发货时效", "动销率", "供应商评估", "达成率", "售后率",
                    "退款率", "发货时长", "上架率"):
        if keyword in query:
            params["query_type"] = "cross"
            if not params.get("metrics"):
                _fill_metric(params, query)
            logger.info(f"L2 query_type 补全: cross (keyword={keyword!r})")
            return

    # trend 关键词
    for keyword in ("趋势", "每天", "每周", "每月", "走势", "变化", "曲线"):
        if keyword in query:
            params["query_type"] = "trend"
            if not params.get("time_granularity"):
                _fill_time_granularity(params, query)
            logger.info(f"L2 query_type 补全: trend (keyword={keyword!r})")
            return

    # compare 关键词
    for keyword in ("环比", "同比", "比上个月", "比去年", "增长率",
                    "上个月", "去年同期"):
        if keyword in query:
            params["query_type"] = "compare"
            if not params.get("compare_range"):
                _fill_compare_range(params, query)
            logger.info(
                f"L2 query_type 补全: compare (keyword={keyword!r})",
            )
            return

    # ratio 关键词
    for keyword in ("占比", "比例", "ABC", "帕累托", "贡献度"):
        if keyword in query:
            params["query_type"] = "ratio"
            logger.info(f"L2 query_type 补全: ratio (keyword={keyword!r})")
            return

    # distribution 关键词
    for keyword in ("分布", "区间", "直方图"):
        if keyword in query:
            params["query_type"] = "distribution"
            logger.info(
                f"L2 query_type 补全: distribution (keyword={keyword!r})",
            )
            return
