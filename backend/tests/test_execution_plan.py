"""
DAG 执行计划 + 意图分析器 单元测试。

覆盖: execution_plan.py / plan_builder.py
设计文档: docs/document/TECH_多Agent单一职责重构.md §9 / §13.7-13.8
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.execution_plan import (
    ExecutionPlan,
    PlanValidationError,
    Round,
    MAX_ROUNDS,
    MAX_AGENTS_PER_ROUND,
    MAX_TOTAL_AGENTS,
)
from services.agent.plan_builder import (
    PlanBuilder,
    build_plan_prompt,
    needs_compute,
    parse_llm_plan,
    quick_classify,
)


# ============================================================
# ExecutionPlan — 数据类
# ============================================================


class TestExecutionPlan:

    def test_single_domain(self):
        plan = ExecutionPlan.single("warehouse", task="查库存")
        assert plan.is_single_domain
        assert not plan.is_abort
        assert plan.total_agents == 1
        assert plan.rounds[0].agents == ["warehouse"]

    def test_abort(self):
        plan = ExecutionPlan.abort("无法理解")
        assert plan.is_abort
        assert plan.abort_message == "无法理解"
        assert plan.total_agents == 0

    def test_multi_domain(self):
        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse", "purchase"], task="并行查", depends_on=[]),
            Round(agents=["compute"], task="汇总", depends_on=[0]),
        ])
        assert not plan.is_single_domain
        assert plan.total_agents == 3

    def test_from_dict(self):
        data = {
            "rounds": [
                {"agents": ["aftersale"], "task": "查退货", "depends_on": []},
                {"agents": ["warehouse", "purchase"], "task": "查库存和采购", "depends_on": [0]},
                {"agents": ["compute"], "task": "汇总", "depends_on": [0, 1]},
            ],
        }
        plan = ExecutionPlan.from_dict(data)
        assert len(plan.rounds) == 3
        assert plan.rounds[1].agents == ["warehouse", "purchase"]
        assert plan.rounds[2].depends_on == [0, 1]

    def test_from_dict_empty(self):
        plan = ExecutionPlan.from_dict({})
        assert len(plan.rounds) == 0

    def test_describe_single(self):
        plan = ExecutionPlan.single("warehouse")
        desc = plan.describe()
        assert "warehouse" in desc
        assert "1 轮" in desc

    def test_describe_abort(self):
        plan = ExecutionPlan.abort("error")
        assert "[ABORT]" in plan.describe()


# ============================================================
# DAG 校验
# ============================================================


class TestPlanValidation:

    def test_valid_single(self):
        plan = ExecutionPlan.single("warehouse")
        plan.validate()  # 不抛异常

    def test_valid_multi_round(self):
        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"]),
            Round(agents=["warehouse", "purchase"], depends_on=[0]),
            Round(agents=["compute"], depends_on=[0, 1]),
        ])
        plan.validate()

    def test_abort_passes_validation(self):
        plan = ExecutionPlan.abort("x")
        plan.validate()

    def test_too_many_rounds(self):
        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse"]) for _ in range(MAX_ROUNDS + 1)
        ])
        with pytest.raises(PlanValidationError, match="轮"):
            plan.validate()

    def test_too_many_agents_per_round(self):
        plan = ExecutionPlan(rounds=[
            Round(agents=["a"] * (MAX_AGENTS_PER_ROUND + 1)),
        ])
        with pytest.raises(PlanValidationError, match="Agent"):
            plan.validate()

    def test_too_many_total_agents(self):
        # 3 rounds × 4 agents = 12 > MAX_TOTAL_AGENTS(10)
        plan = ExecutionPlan(rounds=[
            Round(agents=["a", "b", "c", "d"]),
            Round(agents=["e", "f", "g", "h"], depends_on=[0]),
            Round(agents=["i", "j", "k"], depends_on=[0, 1]),
        ])
        with pytest.raises(PlanValidationError, match="总"):
            plan.validate()

    def test_empty_round_rejected(self):
        plan = ExecutionPlan(rounds=[Round(agents=[])])
        with pytest.raises(PlanValidationError, match="没有 Agent"):
            plan.validate()

    def test_forward_dependency_rejected(self):
        """depends_on 指向当前或后续 Round → 有环"""
        plan = ExecutionPlan(rounds=[
            Round(agents=["a"], depends_on=[1]),
            Round(agents=["b"]),
        ])
        with pytest.raises(PlanValidationError, match="环"):
            plan.validate()

    def test_self_dependency_rejected(self):
        plan = ExecutionPlan(rounds=[
            Round(agents=["a"], depends_on=[0]),
        ])
        with pytest.raises(PlanValidationError, match="环"):
            plan.validate()

    def test_out_of_range_dependency_rejected(self):
        plan = ExecutionPlan(rounds=[
            Round(agents=["a"], depends_on=[5]),
        ])
        with pytest.raises(PlanValidationError):
            plan.validate()

    def test_negative_dependency_rejected(self):
        plan = ExecutionPlan(rounds=[
            Round(agents=["a"], depends_on=[-1]),
        ])
        with pytest.raises(PlanValidationError, match="不存在"):
            plan.validate()


# ============================================================
# quick_classify — 关键词降级
# ============================================================


class TestQuickClassify:

    def test_warehouse_keywords(self):
        assert quick_classify("查一下A001库存") == "warehouse"
        assert quick_classify("哪些缺货") == "warehouse"
        assert quick_classify("仓库列表") == "warehouse"

    def test_purchase_keywords(self):
        assert quick_classify("采购单到货了吗") == "purchase"
        assert quick_classify("供应商列表") == "purchase"

    def test_trade_keywords(self):
        assert quick_classify("今天多少订单") == "trade"
        assert quick_classify("发货情况") == "trade"
        assert quick_classify("物流查询") == "trade"

    def test_aftersale_keywords(self):
        assert quick_classify("退货率多少") == "aftersale"
        assert quick_classify("售后单查询") == "aftersale"

    def test_no_match(self):
        assert quick_classify("hello world") is None
        assert quick_classify("天气怎么样") is None

    def test_highest_score_wins(self):
        """多域都有关键词，得分最高的胜出"""
        # "订单退货退款" → trade(订单+退款) vs aftersale(退货+退款率)
        # trade: 订单(1) + 退款(1) = 2
        # aftersale: 退货(1) = 1
        assert quick_classify("订单退货退款") == "trade"

    def test_ambiguous_tie_returns_none(self):
        """并列得分 → 返回 None（歧义，应由 LLM 处理）"""
        # "采购入库" → purchase(采购=1) vs warehouse(入库=1)
        assert quick_classify("采购入库") is None


# ============================================================
# needs_compute
# ============================================================


class TestNeedsCompute:

    def test_compute_with_data_domain(self):
        """需要同时命中数据域 + 计算关键词"""
        assert needs_compute("库存对比导出Excel")
        assert needs_compute("订单统计分析排名")
        assert needs_compute("采购环比数据")

    def test_compute_keyword_only_not_enough(self):
        """纯计算关键词但无数据域 → 不追加 compute"""
        assert not needs_compute("合并对比导出Excel")
        assert not needs_compute("统计分析排名")

    def test_no_compute(self):
        assert not needs_compute("查一下库存")
        assert not needs_compute("订单详情")

    def test_ambiguous_domain_no_compute(self):
        """并列歧义场景 → quick_classify=None → 不追加"""
        # "采购入库" 同时命中 purchase(采购) 和 warehouse(入库)
        # 并列歧义 → needs_compute 返回 False
        assert not needs_compute("采购入库数量对比")


# ============================================================
# parse_llm_plan
# ============================================================


class TestParseLlmPlan:

    def test_valid_json(self):
        raw = '{"rounds": [{"agents": ["warehouse"], "task": "查库存", "depends_on": []}]}'
        plan = parse_llm_plan(raw)
        assert len(plan.rounds) == 1

    def test_with_markdown_fence(self):
        raw = '```json\n{"rounds": [{"agents": ["trade"], "task": "查订单"}]}\n```'
        plan = parse_llm_plan(raw)
        assert plan.rounds[0].agents == ["trade"]

    def test_invalid_json(self):
        with pytest.raises(PlanValidationError, match="JSON"):
            parse_llm_plan("not json at all")

    def test_missing_rounds(self):
        with pytest.raises(PlanValidationError, match="rounds"):
            parse_llm_plan('{"data": []}')

    def test_unknown_domain_rejected(self):
        raw = '{"rounds": [{"agents": ["finance"], "task": "查财务"}]}'
        with pytest.raises(PlanValidationError, match="未知域"):
            parse_llm_plan(raw)

    def test_complex_plan(self):
        raw = """
        {
          "rounds": [
            {"agents": ["aftersale"], "task": "查退货", "depends_on": []},
            {"agents": ["warehouse", "purchase"], "task": "查库存和采购", "depends_on": [0]},
            {"agents": ["compute"], "task": "汇总导出", "depends_on": [0, 1]}
          ]
        }
        """
        plan = parse_llm_plan(raw)
        assert len(plan.rounds) == 3
        assert plan.total_agents == 4


# ============================================================
# build_plan_prompt
# ============================================================


class TestBuildPlanPrompt:

    def test_contains_query(self):
        prompt = build_plan_prompt("查库存")
        assert "查库存" in prompt

    def test_contains_domains(self):
        prompt = build_plan_prompt("x")
        assert "warehouse" in prompt
        assert "purchase" in prompt
        assert "trade" in prompt
        assert "aftersale" in prompt
        assert "compute" in prompt


# ============================================================
# PlanBuilder — 三级降级链
# ============================================================


class TestPlanBuilder:

    @pytest.mark.asyncio
    async def test_no_adapter_uses_keyword_fallback(self):
        """无 adapter → 跳过 LLM，走关键词"""
        builder = PlanBuilder(adapter=None)
        plan = await builder.build("查一下A001库存")
        assert plan.is_single_domain
        assert plan.rounds[0].agents == ["warehouse"]

    @pytest.mark.asyncio
    async def test_keyword_with_compute(self):
        """关键词匹配 + 需要计算 → 追加 compute Round"""
        builder = PlanBuilder(adapter=None)
        plan = await builder.build("库存导出Excel")
        assert len(plan.rounds) == 2
        assert plan.rounds[0].agents == ["warehouse"]
        assert plan.rounds[1].agents == ["compute"]
        assert plan.rounds[1].depends_on == [0]

    @pytest.mark.asyncio
    async def test_no_match_aborts(self):
        """关键词无法匹配 → abort"""
        builder = PlanBuilder(adapter=None)
        plan = await builder.build("hello world")
        assert plan.is_abort

    @pytest.mark.asyncio
    async def test_llm_success(self):
        """LLM 返回合法 JSON → 直接用"""
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(return_value={
            "content": '{"rounds": [{"agents": ["trade"], "task": "查订单"}]}',
        })
        builder = PlanBuilder(adapter=mock_adapter)
        plan = await builder.build("今天多少订单")
        assert plan.rounds[0].agents == ["trade"]
        mock_adapter.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_keyword(self):
        """LLM 返回垃圾 → 降级到关键词"""
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(return_value={
            "content": "这不是JSON",
        })
        builder = PlanBuilder(adapter=mock_adapter)
        plan = await builder.build("查一下采购单")
        assert plan.rounds[0].agents == ["purchase"]

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self):
        """LLM 调用异常 → 降级到关键词"""
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(side_effect=Exception("timeout"))
        builder = PlanBuilder(adapter=mock_adapter)
        plan = await builder.build("仓库列表")
        assert plan.rounds[0].agents == ["warehouse"]

    @pytest.mark.asyncio
    async def test_tokens_used_accumulated_from_llm(self):
        """LLM 调用后 token 计入 builder.tokens_used"""
        mock_adapter = MagicMock()
        mock_adapter.chat = AsyncMock(return_value={
            "content": '{"rounds": [{"agents": ["trade"], "task": "查订单"}]}',
            "prompt_tokens": 200,
            "completion_tokens": 80,
        })
        builder = PlanBuilder(adapter=mock_adapter)
        assert builder.tokens_used == 0
        await builder.build("今天多少订单")
        assert builder.tokens_used == 280
