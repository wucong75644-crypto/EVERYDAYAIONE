"""
意图分析 → ExecutionPlan 构建器。

三级降级链：
1. LLM 结构化规划（解析 JSON DAG）
2. 关键词匹配单域直通（_quick_classify）
3. abort（无法理解）

设计文档: docs/document/TECH_多Agent单一职责重构.md §13.7
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from loguru import logger

from services.agent.execution_plan import (
    ExecutionPlan,
    PlanValidationError,
)


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

# 需要计算的关键词（追加 compute round）
_COMPUTE_KEYWORDS = [
    "对比", "合并", "汇总", "导出", "Excel", "excel",
    "计算", "统计", "分析", "排名", "环比", "同比",
]

# 有效域名
VALID_DOMAINS = frozenset({
    "warehouse", "purchase", "trade", "aftersale", "compute",
})


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
    # 并列 → 返回 None，走 LLM 或 abort
    if (
        len(sorted_scores) >= 2
        and sorted_scores[0][1] == sorted_scores[1][1]
    ):
        logger.info(
            f"quick_classify ambiguous: {sorted_scores[:3]}",
        )
        return None
    return sorted_scores[0][0]


def needs_compute(query: str) -> bool:
    """判断查询是否需要计算/汇总/导出（需追加 ComputeAgent Round）。

    仅在降级链第二级（关键词单域直通）时使用。
    当 quick_classify 返回 None（无法判断域 或 并列歧义）时，
    本函数也返回 False，不追加 ComputeAgent。
    该场景应由 LLM 第一级处理；若 LLM 也失败，降级链走 abort，
    用户看到"无法理解请求"，不会出现"听懂了但没计算"。
    """
    has_data_domain = quick_classify(query) is not None
    has_compute_kw = any(kw in query.lower() for kw in _COMPUTE_KEYWORDS)
    return has_data_domain and has_compute_kw


def parse_llm_plan(raw_json: str) -> ExecutionPlan:
    """解析 LLM 返回的 JSON 字符串为 ExecutionPlan。

    容错处理：
    - 提取 JSON 块（去除 markdown 代码围栏）
    - 校验域名合法性
    - 校验 DAG 结构
    """
    # 去除 markdown 代码围栏
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_json)
    cleaned = cleaned.replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise PlanValidationError(f"LLM 返回的不是合法 JSON: {e}")

    if not isinstance(data, dict) or "rounds" not in data:
        raise PlanValidationError("LLM 返回格式缺少 rounds 字段")

    plan = ExecutionPlan.from_dict(data)

    # 校验域名合法性
    for i, rnd in enumerate(plan.rounds):
        for agent in rnd.agents:
            if agent not in VALID_DOMAINS:
                raise PlanValidationError(
                    f"Round {i} 包含未知域 '{agent}'，"
                    f"可选: {', '.join(sorted(VALID_DOMAINS))}",
                )

    plan.validate()
    return plan


def build_plan_prompt(query: str) -> str:
    """构建让 LLM 生成执行计划的 prompt。"""
    return (
        "分析以下用户查询，生成执行计划（JSON格式）。\n\n"
        f"用户查询：{query}\n\n"
        "可用域：\n"
        "- warehouse：库存/仓库/出入库/盘点\n"
        "- purchase：采购/供应商/到货/采退\n"
        "- trade：订单/物流/发货\n"
        "- aftersale：退货/退款/售后\n"
        "- compute：计算/汇总/对比/导出Excel（需要前序数据作为输入）\n\n"
        "规则：\n"
        "1. 只涉及一个域 → 单个 Round\n"
        "2. 多个域互不依赖 → 放同一个 Round 并行\n"
        "3. 有依赖关系 → 拆成多个 Round，depends_on 指向前序\n"
        "4. 需要计算/导出 → 最后追加 compute Round\n"
        "5. 最多 5 轮，每轮最多 4 个 Agent\n\n"
        "返回纯 JSON（不要 markdown 围栏）：\n"
        '{"rounds": [{"agents": ["域名"], "task": "任务描述", "depends_on": []}]}'
    )


class PlanBuilder:
    """执行计划构建器（三级降级链）。

    使用方式：
        builder = PlanBuilder(adapter)
        plan = await builder.build(query)
    """

    def __init__(self, adapter: Any = None):
        """adapter: LLM chat adapter（可选，无 adapter 时只走降级链）。"""
        self._adapter = adapter
        self.tokens_used: int = 0

    async def build(self, query: str) -> ExecutionPlan:
        """三级降级链：LLM规划 → 关键词直通 → abort。"""
        # ── 第一级：LLM 规划 ──
        if self._adapter:
            try:
                plan = await self._llm_plan(query)
                return plan
            except (PlanValidationError, Exception) as e:
                logger.warning(f"LLM plan failed, falling back: {e}")

        # ── 第二级：关键词匹配单域直通 ──
        domain = quick_classify(query)
        if domain:
            plan = ExecutionPlan.single(domain, task=query[:50])
            # 检查是否需要追加 compute
            if needs_compute(query):
                from services.agent.execution_plan import Round
                plan.rounds.append(Round(
                    agents=["compute"],
                    task="计算/汇总/导出",
                    depends_on=[0],
                ))
            return plan

        # ── 第三级：无法理解 ──
        return ExecutionPlan.abort(
            "无法理解您的请求，请更具体地描述您要查询的内容",
        )

    async def _llm_plan(self, query: str) -> ExecutionPlan:
        """调 LLM 生成结构化执行计划。"""
        prompt = build_plan_prompt(query)
        messages = [
            {"role": "system", "content": "你是执行计划生成器，只返回JSON。"},
            {"role": "user", "content": prompt},
        ]

        response = await self._adapter.chat(
            messages=messages,
            tools=None,
            temperature=0.0,
        )

        # 收集 token 消耗（供 ERPAgent 汇总计费）
        usage = getattr(response, "usage", None)
        if usage:
            self.tokens_used += getattr(usage, "prompt_tokens", 0)
            self.tokens_used += getattr(usage, "completion_tokens", 0)
        elif isinstance(response, dict):
            self.tokens_used += response.get("prompt_tokens", 0)
            self.tokens_used += response.get("completion_tokens", 0)

        raw = (
            response.get("content", "")
            if isinstance(response, dict)
            else getattr(response, "content", "")
        )
        return parse_llm_plan(raw)
