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


_VALID_MODES = frozenset({"summary", "detail", "export"})
_VALID_DOC_TYPES = frozenset({
    "order", "purchase", "purchase_return", "aftersale",
    "receipt", "shelf",
})
_TIME_RANGE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}$",
)


def _sanitize_params(params: dict) -> dict:
    """宽容校验 Round.params：非法值用默认值替代，不阻断。"""
    if not isinstance(params, dict):
        return {}
    clean: dict = {}
    # mode: 必须是合法枚举，否则默认 summary
    mode = params.get("mode", "summary")
    clean["mode"] = mode if mode in _VALID_MODES else "summary"
    # doc_type: 必须是合法枚举，否则不填（部门 Agent 自己知道）
    doc_type = params.get("doc_type")
    if doc_type and doc_type in _VALID_DOC_TYPES:
        clean["doc_type"] = doc_type
    # time_range: 格式校验，非法的删掉让 extract_time_range 兜底
    tr = params.get("time_range")
    if tr and isinstance(tr, str) and _TIME_RANGE_RE.match(tr.strip()):
        clean["time_range"] = tr.strip()
    # time_col: 透传（下游校验）
    if params.get("time_col"):
        clean["time_col"] = params["time_col"]
    # platform / group_by: 透传
    if params.get("platform"):
        clean["platform"] = params["platform"]
    if params.get("group_by"):
        clean["group_by"] = params["group_by"]
    return clean


def parse_llm_plan(raw_json: str) -> ExecutionPlan:
    """解析 LLM 返回的 JSON 字符串为 ExecutionPlan。

    容错处理：
    - 提取 JSON 块（去除 markdown 代码围栏）
    - 校验域名合法性
    - params 宽容校验（非法值用默认值替代）
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

    # 校验域名合法性 + params 宽容校验
    for i, rnd in enumerate(plan.rounds):
        for agent in rnd.agents:
            if agent not in VALID_DOMAINS:
                raise PlanValidationError(
                    f"Round {i} 包含未知域 '{agent}'，"
                    f"可选: {', '.join(sorted(VALID_DOMAINS))}",
                )
        # params 宽容校验（非法值替代，不报错）
        if rnd.params:
            rnd.params = _sanitize_params(rnd.params)

    plan.validate()
    return plan


def build_plan_prompt(query: str, now_str: str = "") -> str:
    """构建让 LLM 生成执行计划的 prompt。

    now_str: 当前时间字符串（如 "2026-04-17 16:58 周四"），
             注入 prompt 让 LLM 能标准化时间表达。
    """
    time_line = f"当前时间：{now_str}\n\n" if now_str else ""
    return (
        f"{time_line}"
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
        "每个 Round 必须输出 params 对象，包含：\n"
        "- doc_type: order/purchase/purchase_return/aftersale/receipt/shelf（必填）\n"
        "- mode: summary（统计汇总）/ detail（明细列表）（必填）\n"
        "- time_range: 标准化为 YYYY-MM-DD ~ YYYY-MM-DD（必填，根据当前时间推算）\n"
        "- time_col: pay_time（付款时间）/ doc_created_at（创建时间，默认）\n"
        "- platform: taobao/pdd/douyin/jd/kuaishou/xhs/1688（可选）\n"
        "- group_by: shop/platform（可选，仅 summary 模式）\n"
        "- product_code: 商品编码（如用户提到了具体编码则提取）\n"
        "- order_no: 订单号（如用户提到了则提取）\n"
        "- include_invalid: 布尔值，默认 false。仅当用户明确要求'包含全部'或'不排除刷单'时设为 true。\n"
        "  注意：用户问'刷单有多少'不是 include_invalid，而是用 filters 过滤刷单类型。\n"
        "compute 域的 params 可以为空。\n\n"
        "返回纯 JSON（不要 markdown 围栏）：\n"
        '{"rounds": [{"agents": ["trade"], "task": "今日付款订单统计", '
        '"depends_on": [], '
        '"params": {"doc_type":"order","mode":"summary",'
        '"time_range":"2026-04-17 ~ 2026-04-17","time_col":"pay_time"}}]}'
    )


class PlanBuilder:
    """执行计划构建器（三级降级链）。

    使用方式：
        builder = PlanBuilder(adapter, request_ctx=ctx)
        plan = await builder.build(query)
    """

    def __init__(
        self,
        adapter: Any = None,
        request_ctx: Any = None,
    ):
        self._adapter = adapter
        self._request_ctx = request_ctx
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
            # 降级路径：用 RequestContext 构造默认参数
            plan.rounds[0].params = _build_fallback_params(
                query, self._request_ctx, domain=domain,
            )
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
        now_str = ""
        if self._request_ctx:
            now = self._request_ctx.now
            weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            now_str = (
                f"{now.strftime('%Y-%m-%d %H:%M')} "
                f"{weekday[now.weekday()]}"
            )

        prompt = build_plan_prompt(query, now_str=now_str)
        messages = [
            {"role": "system", "content": "你是执行计划生成器，只返回JSON。"},
            {"role": "user", "content": prompt},
        ]

        response = await self._adapter.chat_sync(messages=messages)

        # 收集 token 消耗（供 ERPAgent 汇总计费）
        self.tokens_used += getattr(response, "prompt_tokens", 0)
        self.tokens_used += getattr(response, "completion_tokens", 0)

        raw = getattr(response, "content", "") or ""
        return parse_llm_plan(raw)


_DOMAIN_TIME_COL: dict[str, str] = {
    "trade": "pay_time",
    # 其他域默认 doc_created_at
}


def _build_fallback_params(
    query: str,
    request_ctx: Any = None,
    domain: str = "",
) -> dict:
    """降级路径的最小参数构造（不用 LLM，纯规则）。

    默认今天 + summary。复杂时间表达（"上个月"/"Q1"）在降级路径下
    不处理，用户会看到今天的数据 + 标注"简化查询模式"。
    """
    params: dict = {"mode": "summary"}

    # 时间默认今天
    if request_ctx:
        today = request_ctx.now.strftime("%Y-%m-%d")
    else:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
    params["time_range"] = f"{today} ~ {today}"
    params["time_col"] = _DOMAIN_TIME_COL.get(domain, "doc_created_at")

    # 模式覆盖
    if any(kw in query for kw in ("明细", "列表", "详情", "导出", "Excel")):
        params["mode"] = "detail"

    # 降级标记
    params["_degraded"] = True

    return params
