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


class TestErrorPrefixDetection:
    """A2: 只匹配工具框架生成的错误前缀"""

    def test_tool_failure_prefix_detected(self):
        """工具执行失败前缀应触发"""
        result = "工具执行失败: ConnectionError"
        _error_prefixes = (
            "工具执行失败:", "工具执行超时", "工具参数JSON格式错误:",
            "❌", "Traceback",
        )
        assert result.startswith(_error_prefixes)

    def test_timeout_prefix_detected(self):
        result = "工具执行超时（30秒），请缩小查询范围"
        _error_prefixes = (
            "工具执行失败:", "工具执行超时", "工具参数JSON格式错误:",
            "❌", "Traceback",
        )
        assert result.startswith(_error_prefixes)

    def test_business_data_not_detected(self):
        """业务数据中的"错误"不应触发"""
        result = "商品名称：错误检测仪\n库存：50件"
        _error_prefixes = (
            "工具执行失败:", "工具执行超时", "工具参数JSON格式错误:",
            "❌", "Traceback",
        )
        assert not result.startswith(_error_prefixes)
        assert "Error:" not in result[:100]

    def test_order_remark_with_failure_not_detected(self):
        """订单备注中的"失败"不应触发"""
        result = "订单备注：发货失败请重新安排\n状态：待处理"
        _error_prefixes = (
            "工具执行失败:", "工具执行超时", "工具参数JSON格式错误:",
            "❌", "Traceback",
        )
        assert not result.startswith(_error_prefixes)
        assert "Error:" not in result[:100]

    def test_error_in_content_detected(self):
        """Error: 在前100字符内应触发"""
        result = "查询结果 Error: invalid parameter\n详情..."
        assert "Error:" in result[:100]


# ============================================================
# F1/F2: 路由经验 + 失败记忆
# ============================================================

    # TestFetchAllPagesVisibility 已删除（_prepare_tools 随旧 tool loop 移除）


# TestStagingCleanup removed — _cleanup_staging_delayed was removed (NAS replaced it)


class TestRecordAgentExperience:
    """F1/F2: ExperienceRecorder（从 ERPAgent 提取）"""

    def _make_recorder(self):
        from services.agent.experience_recorder import ExperienceRecorder
        return ExperienceRecorder(
            db=MagicMock(), org_id="org1", writer="erp_agent",
        )

    @pytest.mark.asyncio
    async def test_routing_experience_calls_add_knowledge(self):
        """成功路由 → category=experience / node_type=routing_pattern / subcategory=业务域"""
        recorder = self._make_recorder()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock, return_value="node1") as mock_add:
            await recorder.record(
                "routing", "查库存", ["local_product_identify", "local_stock_query"],
                "轮次：2", confidence=0.6,
            )
            mock_add.assert_called_once()
            call_kwargs = mock_add.call_args[1]
            assert call_kwargs["category"] == "experience"
            assert call_kwargs["node_type"] == "routing_pattern"
            assert call_kwargs["subcategory"] == "product"
            assert call_kwargs["confidence"] == 0.6
            assert call_kwargs["max_per_node_type"] == 400
            assert "max_per_category" not in call_kwargs
            assert "local_product_identify → local_stock_query" in call_kwargs["content"]
            assert call_kwargs["source"] == "auto"
            assert call_kwargs["metadata"]["writer"] == "erp_agent"
            assert call_kwargs["metadata"]["record_type"] == "routing"

    @pytest.mark.asyncio
    async def test_failure_memory_calls_add_knowledge(self):
        """失败记忆 → category=experience / node_type=failure_pattern / max_per_node_type=200"""
        recorder = self._make_recorder()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock, return_value="node2") as mock_add:
            await recorder.record(
                "failure", "查订单", ["local_order_query"],
                "失败原因：超时",
            )
            call_kwargs = mock_add.call_args[1]
            assert call_kwargs["category"] == "experience"
            assert call_kwargs["node_type"] == "failure_pattern"
            assert call_kwargs["subcategory"] == "order"
            assert call_kwargs["confidence"] == 0.5
            assert call_kwargs["max_per_node_type"] == 200
            assert "查询失败" in call_kwargs["title"]
            assert call_kwargs["source"] == "auto"
            assert call_kwargs["metadata"]["writer"] == "erp_agent"
            assert call_kwargs["metadata"]["record_type"] == "failure"

    @pytest.mark.asyncio
    async def test_knowledge_error_does_not_raise(self):
        """知识库写入失败不抛异常"""
        recorder = self._make_recorder()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock, side_effect=Exception("DB down")):
            await recorder.record(
                "routing", "查库存", ["local_stock_query"], "轮次：1",
            )

    @pytest.mark.asyncio
    async def test_schema_violation_does_not_raise(self):
        """schema 违反（ValueError）也不应冒泡"""
        recorder = self._make_recorder()
        with patch(
            "services.knowledge_service.add_knowledge",
            new_callable=AsyncMock,
            side_effect=ValueError("invalid node_type"),
        ):
            await recorder.record(
                "routing", "q", ["local_stock_query"], "detail",
            )

    @pytest.mark.asyncio
    async def test_max_per_node_type_passed(self):
        """routing/failure 用不同配额"""
        recorder = self._make_recorder()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock) as mock_add:
            await recorder.record(
                "routing", "q", ["local_stock_query"], "detail",
            )
            assert mock_add.call_args[1]["max_per_node_type"] == 400

            mock_add.reset_mock()
            await recorder.record(
                "failure", "q", ["local_order_query"], "detail",
            )
            assert mock_add.call_args[1]["max_per_node_type"] == 200

    @pytest.mark.asyncio
    async def test_unknown_record_type_returns_silently(self):
        """未知 record_type 不调 add_knowledge 也不抛异常"""
        recorder = self._make_recorder()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock) as mock_add:
            await recorder.record(
                "unknown_type", "q", ["local_stock_query"], "detail",
            )
            mock_add.assert_not_called()


class TestInferBusinessDomain:
    """tool_name → business domain 推断测试（现在是独立函数）"""

    def test_local_query_extraction(self):
        from services.agent.experience_recorder import infer_business_domain
        assert infer_business_domain(["local_stock_query"]) == "stock"
        assert infer_business_domain(["local_order_query"]) == "order"
        assert infer_business_domain(["local_product_identify"]) == "product"
        assert infer_business_domain(["local_purchase_query"]) == "purchase"
        assert infer_business_domain(["local_aftersale_query"]) == "aftersale"

    def test_erp_remote_query_extraction(self):
        from services.agent.experience_recorder import infer_business_domain
        assert infer_business_domain(["erp_warehouse_query"]) == "warehouse"
        assert infer_business_domain(["erp_info_query"]) == "info"

    def test_normalization(self):
        from services.agent.experience_recorder import infer_business_domain
        assert infer_business_domain(["erp_aftersales_query"]) == "aftersale"
        assert infer_business_domain(["erp_trade_query"]) == "order"

    def test_first_match_wins(self):
        from services.agent.experience_recorder import infer_business_domain
        assert infer_business_domain(
            ["local_product_identify", "local_stock_query"]
        ) == "product"

    def test_empty_list_returns_general(self):
        from services.agent.experience_recorder import infer_business_domain
        assert infer_business_domain([]) == "general"

    def test_unknown_tool_returns_general(self):
        from services.agent.experience_recorder import infer_business_domain
        assert infer_business_domain(["some_random_tool"]) == "general"
        assert infer_business_domain(["route_to_chat"]) == "general"


# ── Export 子进程隔离相关测试 ────────────────────────────────


class TestExportSubprocessTimeout:
    """erp_agent.run_step 对 export 模式的超时放宽。"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(db=MagicMock(), user_id="u1", conversation_id="c1", org_id="org1")

    @pytest.mark.asyncio
    async def test_export_mode_gets_130s_timeout(self):
        """export 模式的 step 应获得 130s 超时而非默认 30s。"""
        from services.agent.erp_agent import ERPAgent, ExecutionPlan, PlanStep
        agent = self._make_agent()

        step = PlanStep(domain="trade", params={"mode": "export", "doc_type": "order"})
        plan = ExecutionPlan(steps=[step])

        captured_timeout = []

        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout):
            captured_timeout.append(timeout)
            raise asyncio.TimeoutError()  # 不真正执行

        mock_child = MagicMock()
        mock_child.execute = AsyncMock(return_value=MagicMock())
        mock_child._push_thinking = AsyncMock()

        with patch.object(agent, "_create_agent", return_value=mock_child), \
             patch("services.agent.erp_agent.asyncio.wait_for", side_effect=spy_wait_for):
            import time
            results = await agent._execute_plan(plan, "test", time.monotonic() + 180)

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == 130.0

    @pytest.mark.asyncio
    async def test_summary_mode_keeps_30s_timeout(self):
        """summary 模式的 step 保持 30s 超时。"""
        from services.agent.erp_agent import ERPAgent, ExecutionPlan, PlanStep
        agent = self._make_agent()

        step = PlanStep(domain="trade", params={"mode": "summary", "doc_type": "order"})
        plan = ExecutionPlan(steps=[step])

        captured_timeout = []

        async def spy_wait_for(coro, timeout):
            captured_timeout.append(timeout)
            raise asyncio.TimeoutError()

        mock_child = MagicMock()
        mock_child.execute = AsyncMock()
        mock_child._push_thinking = AsyncMock()

        with patch.object(agent, "_create_agent", return_value=mock_child), \
             patch("services.agent.erp_agent.asyncio.wait_for", side_effect=spy_wait_for):
            import time
            results = await agent._execute_plan(plan, "test", time.monotonic() + 180)

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == 30.0


class TestCreateAgentPushThinking:
    """_create_agent 注入 _push_thinking 回调。"""

    def test_child_agent_has_push_thinking(self):
        """子 agent 应被注入父 ERPAgent 的 _push_thinking 方法。"""
        from services.agent.erp_agent import ERPAgent
        from services.agent.departments.trade_agent import TradeAgent
        parent = ERPAgent(db=MagicMock(), user_id="u1", conversation_id="c1", org_id="org1")

        # 用真实的 TradeAgent 实例（不 mock），验证属性注入
        with patch("core.workspace.resolve_staging_dir", return_value="/tmp"):
            child = parent._create_agent("trade")

        assert isinstance(child, TradeAgent)
        # bound method 每次访问产生新对象，比较底层函数和绑定实例
        assert child._push_thinking.__func__ is parent._push_thinking.__func__
        assert child._push_thinking.__self__ is parent
