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


class TestBuildMultiResult:
    """ERPAgent._build_multi_result 结果聚合"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_single_success(self):
        """单步成功 → AgentResult 直传"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_output = MagicMock()
        mock_output.summary = "100 笔订单"
        mock_output.status = "ok"
        mock_output.format = MagicMock(value="text")
        mock_output.file_ref = None
        mock_output.data = None
        mock_output.columns = None
        plan = ExecutionPlan(steps=[PlanStep("trade", {})])
        result = agent._build_multi_result([("trade", mock_output)], plan, "query")
        assert result.status == "success"
        assert result.summary == "100 笔订单"

    @pytest.mark.asyncio
    async def test_multi_success_with_compute_hint(self):
        """多步成功 + compute_hint → metadata 包含 hint"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_trade = MagicMock(summary="订单 100", status="ok", file_ref=None)
        mock_aftersale = MagicMock(summary="售后 10", status="ok", file_ref=None)
        plan = ExecutionPlan(
            steps=[PlanStep("trade", {}), PlanStep("aftersale", {})],
            compute_hint="退货率 = 售后/订单",
        )
        result = agent._build_multi_result(
            [("trade", mock_trade), ("aftersale", mock_aftersale)], plan, "q",
        )
        assert result.status == "success"
        assert "订单" in result.summary
        assert "售后" in result.summary
        assert result.metadata.get("compute_hint") == "退货率 = 售后/订单"

    @pytest.mark.asyncio
    async def test_all_errors(self):
        """全部失败 → error"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        import asyncio
        agent = self._make_agent()
        plan = ExecutionPlan(steps=[PlanStep("trade", {})])
        result = agent._build_multi_result(
            [("trade", asyncio.TimeoutError())], plan, "q",
        )
        assert result.status == "error"
        assert "超时" in result.summary

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        """部分失败 → 成功的照常返回 + 附带错误提示"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_ok = MagicMock(summary="数据", status="ok", format=MagicMock(value="text"),
                           file_ref=None, data=None, columns=None)
        plan = ExecutionPlan(steps=[PlanStep("trade", {}), PlanStep("aftersale", {})])
        result = agent._build_multi_result(
            [("trade", mock_ok), ("aftersale", ConnectionError("网络"))],
            plan, "q",
        )
        assert result.status == "success"
        assert "数据" in result.summary
        assert "售后" in result.summary

    @pytest.mark.asyncio
    async def test_rejected_treated_as_error_with_suggestions(self):
        """REJECTED 状态 → 归入 errors，透传 suggestions"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_rejected = MagicMock()
        mock_rejected.status = "rejected"
        mock_rejected.summary = "数据量过大（预估 6,000,000 行）"
        mock_rejected.metadata = {
            "suggestions": ["缩小时间范围", "添加过滤条件"],
        }
        plan = ExecutionPlan(steps=[PlanStep("trade", {})])
        result = agent._build_multi_result(
            [("trade", mock_rejected)], plan, "q",
        )
        assert result.status == "error"
        assert "数据量过大" in result.summary
        assert "缩小时间范围" in result.summary

    @pytest.mark.asyncio
    async def test_rejected_with_success_partial(self):
        """一个域 REJECTED + 另一个域成功 → 成功的返回 + REJECTED 提示"""
        from services.agent.erp_agent import PlanStep, ExecutionPlan
        agent = self._make_agent()
        mock_ok = MagicMock(summary="库存 OK", status="ok", format=MagicMock(value="text"),
                           file_ref=None, data=None, columns=None)
        mock_rejected = MagicMock()
        mock_rejected.status = "rejected"
        mock_rejected.summary = "数据量过大"
        mock_rejected.metadata = {"suggestions": ["缩小范围"]}
        plan = ExecutionPlan(steps=[PlanStep("warehouse", {}), PlanStep("trade", {})])
        result = agent._build_multi_result(
            [("warehouse", mock_ok), ("trade", mock_rejected)], plan, "q",
        )
        assert result.status == "success"
        assert "库存 OK" in result.summary
        assert "数据量过大" in result.summary


class TestParseMultiExtractResponse:
    """parse_multi_extract_response 解析测试"""

    def test_single_step(self):
        from services.agent.plan_builder import parse_multi_extract_response
        import json
        steps, hint, dep = parse_multi_extract_response(json.dumps({
            "steps": [{"domain": "trade", "params": {"doc_type": "order"}}],
        }))
        assert len(steps) == 1
        assert steps[0][0] == "trade"
        assert hint is None
        assert dep == "parallel"

    def test_multi_step_with_hint(self):
        from services.agent.plan_builder import parse_multi_extract_response
        import json
        steps, hint, dep = parse_multi_extract_response(json.dumps({
            "steps": [
                {"domain": "trade", "params": {}},
                {"domain": "aftersale", "params": {}},
            ],
            "compute_hint": "关联分析",
        }))
        assert len(steps) == 2
        assert hint == "关联分析"
        assert dep == "parallel"

    def test_backward_compat_old_format(self):
        """旧格式 {"domain":..., "params":...} → 自动包装为单步"""
        from services.agent.plan_builder import parse_multi_extract_response
        import json
        steps, hint, dep = parse_multi_extract_response(json.dumps({
            "domain": "warehouse", "params": {"mode": "summary"},
        }))
        assert len(steps) == 1
        assert steps[0][0] == "warehouse"
        assert hint is None
        assert dep == "parallel"

    def test_invalid_domain(self):
        from services.agent.plan_builder import parse_multi_extract_response
        import json
        with pytest.raises(ValueError, match="未知域"):
            parse_multi_extract_response(json.dumps({
                "steps": [{"domain": "unknown", "params": {}}],
            }))

    def test_max_4_steps(self):
        from services.agent.plan_builder import parse_multi_extract_response
        import json
        steps, _, dep = parse_multi_extract_response(json.dumps({
            "steps": [{"domain": "trade", "params": {}}] * 6,
        }))
        assert len(steps) == 4

    def test_serial_dependency(self):
        """显式 serial dependency 正确解析"""
        from services.agent.plan_builder import parse_multi_extract_response
        import json
        steps, hint, dep = parse_multi_extract_response(json.dumps({
            "steps": [
                {"domain": "purchase", "params": {
                    "_expected_output": "商品编码",
                    "_dependencies": [],
                }},
                {"domain": "trade", "params": {
                    "_dependencies": [1],
                    "_required_input": {"from_step": 1, "field": "product_code"},
                }},
            ],
            "compute_hint": "先查采购再查订单",
            "dependency": "serial",
        }))
        assert len(steps) == 2
        assert dep == "serial"
        assert steps[1][1]["_required_input"]["field"] == "product_code"

    def test_dependency_auto_correct(self):
        """LLM 输出 _required_input 但 dependency 标 parallel → 自动纠正为 serial"""
        from services.agent.plan_builder import parse_multi_extract_response
        import json
        _, _, dep = parse_multi_extract_response(json.dumps({
            "steps": [
                {"domain": "purchase", "params": {}},
                {"domain": "trade", "params": {
                    "_required_input": {"from_step": 1, "field": "product_code"},
                }},
            ],
            "dependency": "parallel",
        }))
        assert dep == "serial"



class TestLlmExtract:
    """ERPAgent._llm_extract LLM 调用"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(db=MagicMock(), user_id="u1", conversation_id="c1", org_id="org1")

    @pytest.mark.asyncio
    async def test_llm_returns_valid_json(self):
        """LLM 正常返回 JSON → 解析为 steps + compute_hint"""
        import json
        agent = self._make_agent()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "steps": [{"domain": "trade", "params": {"doc_type": "order"}}],
        })
        mock_response.prompt_tokens = 100
        mock_response.completion_tokens = 50

        mock_adapter = MagicMock()
        mock_adapter.chat_sync = AsyncMock(return_value=mock_response)
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter):
            steps, hint, dep = await agent._llm_extract("今天多少订单")
        assert len(steps) == 1
        assert steps[0][0] == "trade"
        assert dep == "parallel"
        assert agent._tokens_used == 150
        mock_adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_adapter_error_raises(self):
        """adapter 异常 → 抛出，由 _extract_plan 降级处理"""
        agent = self._make_agent()
        mock_adapter = MagicMock()
        mock_adapter.chat_sync = AsyncMock(side_effect=ConnectionError("API 不可用"))
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter):
            with pytest.raises(ConnectionError):
                await agent._llm_extract("查库存")
        mock_adapter.close.assert_awaited_once()  # finally 保证关闭


class TestCreateAgent:
    """ERPAgent._create_agent DepartmentAgent 工厂"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(db=MagicMock(), user_id="u1", conversation_id="c1", org_id="org1")

    def test_four_domains_create_correct_agents(self):
        """4 个域各创建对应的 DepartmentAgent"""
        agent = self._make_agent()
        with patch("core.workspace.resolve_staging_dir", return_value="/tmp/staging"):
            trade = agent._create_agent("trade")
            purchase = agent._create_agent("purchase")
            warehouse = agent._create_agent("warehouse")
            aftersale = agent._create_agent("aftersale")
        assert trade.__class__.__name__ == "TradeAgent"
        assert purchase.__class__.__name__ == "PurchaseAgent"
        assert warehouse.__class__.__name__ == "WarehouseAgent"
        assert aftersale.__class__.__name__ == "AftersaleAgent"

    def test_unknown_domain_returns_none(self):
        """未知域 → None"""
        agent = self._make_agent()
        assert agent._create_agent("finance") is None
        assert agent._create_agent("") is None

    def test_staging_dir_injected(self):
        """staging_dir 正确注入到 DepartmentAgent"""
        agent = self._make_agent()
        with patch("core.workspace.resolve_staging_dir", return_value="/tmp/test_staging"):
            created = agent._create_agent("trade")
        assert created._staging_dir == "/tmp/test_staging"


class TestPushThinking:
    """ERPAgent._push_thinking 进度推送"""

    def _make_agent(self, task_id=None, message_id=None):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1", conversation_id="c1", org_id="org1",
            task_id=task_id, message_id=message_id,
        )

    @pytest.mark.asyncio
    async def test_collects_text_with_task_id(self):
        """有 task_id+message_id → 收集到 _thinking_parts（不再推送 WS）"""
        agent = self._make_agent(task_id="t1", message_id="m1")
        await agent._push_thinking("查询中...")
        assert "→ 查询中..." in agent._thinking_parts

    @pytest.mark.asyncio
    async def test_silent_without_task_id(self):
        """无 task_id → 只收集文本，不推送 WS"""
        agent = self._make_agent()
        await agent._push_thinking("测试")
        assert "→ 测试" in agent._thinking_parts
        # 没有 ws_manager 调用（无 task_id 直接 return）
