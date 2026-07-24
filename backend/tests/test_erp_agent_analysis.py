"""
ERPAgent 单元测试

覆盖：filter_erp_context, ERPAgent.execute,
      ToolExecutor._erp_agent handler, erp_agent 工具注册
"""

import asyncio
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================
# filter_erp_context 上下文筛选
# ============================================================


class TestAnalyze:
    """ERPAgent.analyze() — 分析接口，只分析不执行"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_analyze_serial_returns_plan(self):
        """analyze() 返回 status=plan，不调 _execute_plan"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                [
                    ("purchase", {"doc_type": "purchase", "mode": "summary",
                     "_expected_output": "商品编码", "_dependencies": []}),
                    ("trade", {"doc_type": "order", "mode": "summary",
                     "_dependencies": [1],
                     "_required_input": {"from_step": 1, "field": "product_code"}}),
                ],
                "先查采购再查订单",
                "serial",
            )
            result = await agent.analyze("查供应商商品再查订单")
        assert result.status == "plan"
        assert "能力约束" in result.summary
        assert result.metadata["reason"] == "串行依赖"
        assert len(result.metadata["plan_steps"]) == 2

    @pytest.mark.asyncio
    async def test_analyze_single_step_also_returns_plan(self):
        """analyze() 即使单步也返回 plan（分析接口始终返回结构化分析）"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                [("trade", {"doc_type": "order", "mode": "summary"})],
                None,
                "parallel",
            )
            result = await agent.analyze("今天多少订单")
        assert result.status == "plan"
        assert len(result.metadata["plan_steps"]) == 1

    @pytest.mark.asyncio
    async def test_analyze_does_not_execute(self):
        """analyze() 不调 _execute_plan（不查数据库）"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                [("trade", {"doc_type": "order"}), ("aftersale", {"doc_type": "aftersale"})],
                "退货率",
                "parallel",
            )
            with patch.object(agent, "_execute_plan", new_callable=AsyncMock) as mock_exec:
                result = await agent.analyze("退货率")
        mock_exec.assert_not_called()
        assert result.status == "plan"

    @pytest.mark.asyncio
    async def test_execute_no_longer_shortcircuits(self):
        """_execute() 不再短路——serial 2步也直接执行"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                [
                    ("purchase", {"doc_type": "purchase"}),
                    ("trade", {"doc_type": "order"}),
                ],
                "先查采购再查订单",
                "serial",
            )
            mock_result = MagicMock()
            mock_result.summary = "数据"
            mock_result.status = "ok"
            mock_result.format = "text"
            mock_result.file_ref = None
            mock_result.data = None
            mock_result.columns = None
            with patch.object(
                agent, "_execute_plan", new_callable=AsyncMock,
                return_value=[("purchase", mock_result), ("trade", mock_result)],
            ) as mock_exec:
                import time
                result = await agent._execute(
                    "查供应商商品再查订单",
                    deadline=time.monotonic() + 30,
                )
        # L2 短路已删除，serial 也直接执行
        mock_exec.assert_called_once()
        assert result.status != "plan"

    def test_build_analyze_result_structure(self):
        """_build_analyze_result 返回结构正确"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        plan = ExecutionPlan(
            steps=[
                PlanStep("purchase", {
                    "doc_type": "purchase", "mode": "summary",
                    "supplier_name": "纸制品01",
                    "_expected_output": "商品编码列表",
                    "_dependencies": [],
                }),
                PlanStep("trade", {
                    "doc_type": "order", "mode": "summary",
                    "_expected_output": "订单数据",
                    "_dependencies": [1],
                    "_required_input": {"from_step": 1, "field": "product_code"},
                }),
            ],
            compute_hint="先查采购再查订单",
            dependency="serial",
        )
        result = agent._build_analyze_result(plan, "测试查询")
        assert result.status == "plan"
        assert result.source == "erp_agent"
        assert result.confidence == 1.0
        assert "采购" in result.summary
        assert "步骤1" in result.summary
        meta = result.metadata
        assert meta["reason"] == "串行依赖"
        assert len(meta["plan_steps"]) == 2
        step2 = meta["plan_steps"][1]
        assert step2["dependencies"] == [1]
        assert step2["required_input"]["field"] == "product_code"
        assert "_expected_output" not in step2["params"]


class TestAnalyzeE2E:
    """analyze() 端到端集成测试 — 模拟 LLM 返回 → analyze 接口 → 序列化输出"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_analyze_serial_e2e(self):
        """模拟真实场景：LLM 返回 serial JSON → analyze 接口 → 主 Agent 收到的内容"""
        import json
        agent = self._make_agent()

        llm_response_json = json.dumps({
            "steps": [
                {"domain": "purchase", "params": {
                    "doc_type": "purchase", "mode": "summary",
                    "time_range": "2026-04-01 ~ 2026-04-25",
                    "supplier_name": "纸制品01", "group_by": "product",
                    "_expected_output": "商品编码列表（product_code）",
                    "_dependencies": [],
                }},
                {"domain": "trade", "params": {
                    "doc_type": "order", "mode": "summary",
                    "time_range": "2026-04-01 ~ 2026-04-25",
                    "_expected_output": "订单数据",
                    "_dependencies": [1],
                    "_required_input": {"from_step": 1, "field": "product_code"},
                }},
            ],
            "compute_hint": "先查供应商采购商品获取编码，再用编码查订单",
            "dependency": "serial",
        })

        mock_response = MagicMock()
        mock_response.content = llm_response_json
        mock_response.prompt_tokens = 200
        mock_response.completion_tokens = 100

        mock_adapter = MagicMock()
        mock_adapter.chat_sync = AsyncMock(return_value=mock_response)
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter):
            result = await agent.analyze("查供应商纸制品01的采购商品，用编码查订单")

        # 1. 返回 plan 状态
        assert result.status == "plan"
        assert result.source == "erp_agent"

        # 2. summary 内容
        assert "[能力约束" in result.summary
        assert "纸制品01" in result.summary
        assert "product_code" in result.summary

        # 3. metadata 结构
        meta = result.metadata
        assert meta["reason"] == "串行依赖"
        assert len(meta["plan_steps"]) == 2
        assert meta["plan_steps"][1]["dependencies"] == [1]

        # 4. 序列化格式
        blocks = result.to_message_content()
        assert "[能力约束" in blocks[0]["text"]
        assert all("[文件已存入 staging" not in b["text"] for b in blocks)

    @pytest.mark.asyncio
    async def test_analyze_auto_correct_serial(self):
        """LLM 标 parallel 但有 _required_input → 自动纠正为 serial"""
        import json
        agent = self._make_agent()

        llm_json = json.dumps({
            "steps": [
                {"domain": "purchase", "params": {"doc_type": "purchase", "mode": "summary",
                    "time_range": "2026-04-01 ~ 2026-04-25",
                    "_expected_output": "商品编码"}},
                {"domain": "trade", "params": {"doc_type": "order", "mode": "summary",
                    "time_range": "2026-04-01 ~ 2026-04-25",
                    "_required_input": {"from_step": 1, "field": "product_code"}}},
            ],
            "dependency": "parallel",
        })

        mock_response = MagicMock()
        mock_response.content = llm_json
        mock_response.prompt_tokens = 100
        mock_response.completion_tokens = 60
        mock_adapter = MagicMock()
        mock_adapter.chat_sync = AsyncMock(return_value=mock_response)
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter):
            result = await agent.analyze("查供应商商品再查订单")

        assert result.status == "plan"
        assert result.metadata["reason"] == "串行依赖"

    def test_malformed_required_input_no_crash(self):
        """_required_input 结构不完整时不崩溃"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        plan = ExecutionPlan(
            steps=[
                PlanStep("purchase", {"doc_type": "purchase"}),
                PlanStep("trade", {
                    "doc_type": "order",
                    "_required_input": {"from_step": 1},
                }),
            ],
            compute_hint="测试",
            dependency="serial",
        )
        result = agent._build_analyze_result(plan, "测试")
        assert result.status == "plan"
        assert "步骤1" in result.summary
        assert "?" in result.summary

    def test_non_dict_required_input_no_crash(self):
        """_required_input 不是 dict 时不崩溃"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        plan = ExecutionPlan(
            steps=[
                PlanStep("purchase", {"doc_type": "purchase"}),
                PlanStep("trade", {
                    "doc_type": "order",
                    "_required_input": "invalid_string",
                    "_dependencies": "also_invalid",
                }),
            ],
            dependency="serial",
        )
        result = agent._build_analyze_result(plan, "测试")
        assert result.status == "plan"
        step2 = result.metadata["plan_steps"][1]
        assert step2["required_input"] is None
        assert step2["dependencies"] == []


class TestExecutePlan:
    """ERPAgent._execute_plan 并行执行"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_single_step_success(self):
        """单步执行成功"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_output = MagicMock()
        mock_output.summary = "订单 100 笔"
        mock_output.status = "ok"
        mock_output.format = "text"
        mock_output.file_ref = None
        mock_output.data = None
        mock_output.columns = None
        mock_agent = MagicMock()
        mock_agent.execute = AsyncMock(return_value=mock_output)
        with patch.object(agent, "_create_agent", return_value=mock_agent):
            import time
            results = await agent._execute_plan(
                ExecutionPlan(steps=[PlanStep("trade", {"mode": "summary"})]),
                "今天多少订单", time.monotonic() + 30,
            )
        assert len(results) == 1
        assert results[0][0] == "trade"
        assert results[0][1].summary == "订单 100 笔"

    @pytest.mark.asyncio
    async def test_parallel_multi_step(self):
        """多步并行执行"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_trade = MagicMock()
        mock_trade.summary = "订单 100 笔"
        mock_trade.status = "ok"
        mock_aftersale = MagicMock()
        mock_aftersale.summary = "售后 10 笔"
        mock_aftersale.status = "ok"

        call_count = 0
        def make_agent(domain):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.execute = AsyncMock(return_value=mock_trade if domain == "trade" else mock_aftersale)
            return m

        with patch.object(agent, "_create_agent", side_effect=make_agent):
            import time
            results = await agent._execute_plan(
                ExecutionPlan(steps=[
                    PlanStep("trade", {}), PlanStep("aftersale", {}),
                ]),
                "退货率", time.monotonic() + 30,
            )
        assert len(results) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_step_exception_captured(self):
        """单步异常不影响其他步骤"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_ok = MagicMock()
        mock_ok.summary = "数据"
        mock_ok.status = "ok"

        def make_agent(domain):
            m = MagicMock()
            if domain == "trade":
                m.execute = AsyncMock(return_value=mock_ok)
            else:
                m.execute = AsyncMock(side_effect=ConnectionError("网络错误"))
            return m

        with patch.object(agent, "_create_agent", side_effect=make_agent):
            import time
            results = await agent._execute_plan(
                ExecutionPlan(steps=[PlanStep("trade", {}), PlanStep("aftersale", {})]),
                "test", time.monotonic() + 30,
            )
        # trade 成功，aftersale 是 Exception
        trade_result = [r for r in results if r[0] == "trade"][0]
        aftersale_result = [r for r in results if r[0] == "aftersale"][0]
        assert trade_result[1].summary == "数据"
        assert isinstance(aftersale_result[1], Exception)
