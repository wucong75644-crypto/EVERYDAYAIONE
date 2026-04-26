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


def _is_negated(query: str, keyword: str) -> bool:
    """检查 query 中 keyword 前面是否有否定词（非/不是/除了/排除/不含）。

    "非" 单字必须紧接 keyword（"非淘宝"✓ "非常好的淘宝"✗），
    多字否定词在 keyword 前 4 字符内出现即匹配。
    """
    idx = query.find(keyword)
    if idx < 0:
        return False
    prefix = query[max(0, idx - 4):idx]
    # 多字否定词：前 4 字符内出现即可
    if any(neg in prefix for neg in ("不是", "除了", "排除", "不含")):
        return True
    # 单字 "非"：必须紧接 keyword（idx-1 位置）
    return idx >= 1 and query[idx - 1] == "非"


def fill_platform(params: dict, query: str) -> None:
    """L2 意图完整性：从用户查询文本补全 LLM 漏提取的 platform。

    纯函数，不依赖 ERPAgent 实例。供外部调用或测试使用。
    仅补全正向平台过滤（"淘宝订单"→platform=tb），
    否定语境（"非淘宝""除了京东"）和已有排除条件时不补全。
    """
    if params.get("platform"):
        return  # AI 已提取 platform 正向过滤，不覆盖

    # exclude_filters 中已包含 platform → LLM 已处理平台排除条件，不补全
    for ef in params.get("exclude_filters", []):
        if ef.get("field") == "platform":
            return

    from services.kuaimai.erp_unified_schema import PLATFORM_NORMALIZE
    cn_keys = [
        k for k in PLATFORM_NORMALIZE
        if not k.isascii() or k == "1688"
    ]
    matched: set[str] = set()
    for key in cn_keys:
        if key in query and not _is_negated(query, key):
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
