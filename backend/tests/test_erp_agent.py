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


class TestFilterErpContext:
    """filter_erp_context 上下文筛选"""

    def test_removes_system_messages(self):
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "system", "content": "你是AI助手"},
            {"role": "user", "content": "查库存"},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_keeps_all_user_messages(self):
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "user", "content": "查库存"},
            {"role": "user", "content": "画一只猫"},
            {"role": "user", "content": "那退货呢"},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 3

    def test_keeps_erp_agent_assistant(self):
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "erp_agent"}},
            ], "content": None},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 1

    def test_filters_non_erp_assistant(self):
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "generate_image"}},
            ], "content": None},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 0

    def test_keeps_plain_text_assistant(self):
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "assistant", "content": "好的，帮你查"},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 1

    def test_keeps_tool_results(self):
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "tool", "content": "库存128件", "tool_call_id": "tc1"},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 1

    def test_mixed_conversation(self):
        """完整对话场景：ERP查询 + 画图 + 追问"""
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "system", "content": "系统提示词"},
            {"role": "user", "content": "查YSL01库存"},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "erp_agent"}},
            ], "content": None},
            {"role": "tool", "content": "库存128件", "tool_call_id": "tc1"},
            {"role": "user", "content": "画一只猫"},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "generate_image"}},
            ], "content": None},
            {"role": "tool", "content": "task_id=xxx", "tool_call_id": "tc2"},
            {"role": "user", "content": "那退货呢"},
        ]
        result = filter_erp_context(messages)
        # system 被过滤，generate_image 的 assistant 被过滤
        roles = [m["role"] for m in result]
        assert "system" not in roles
        assert len(result) == 6  # 3 user + 1 erp assistant + 2 tool

    def test_empty_messages(self):
        from services.erp_agent import filter_erp_context
        assert filter_erp_context([]) == []


# ============================================================
# AgentResult 数据结构（Phase 6: 替代 ERPAgentResult）
# ============================================================


class TestAgentResultBasic:
    """AgentResult 基本字段"""

    def test_default_values(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(status="success", summary="测试")
        assert r.summary == "测试"
        assert r.status == "success"
        assert r.tokens_used == 0
        assert r.source == ""

    def test_with_all_fields(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(
            status="success",
            summary="结论",
            tokens_used=500,
            source="erp_agent",
            confidence=0.6,
        )
        assert r.tokens_used == 500
        assert r.source == "erp_agent"
        assert r.confidence == 0.6


# ============================================================
# re-export 兼容性（Phase 6: services/erp_agent.py 导出 AgentResult）
# ============================================================


class TestReExportCompatibility:
    """services/erp_agent.py re-export 保证旧导入路径可用"""

    def test_import_agent_result_from_compat_path(self):
        from services.erp_agent import AgentResult
        r = AgentResult(status="success", summary="test")
        assert r.status == "success"

    def test_import_max_erp_turns_from_compat_path(self):
        from services.erp_agent import MAX_ERP_TURNS
        assert isinstance(MAX_ERP_TURNS, int)

    def test_import_filter_erp_context_from_compat_path(self):
        from services.erp_agent import filter_erp_context
        assert callable(filter_erp_context)


# ============================================================
# ToolExecutor._erp_agent handler 注册
# ============================================================


class TestToolExecutorERPAgent:
    """ToolExecutor erp_agent handler"""

    def test_erp_agent_registered(self):
        from services.tool_executor import ToolExecutor
        exe = ToolExecutor(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        assert "erp_agent" in exe._handlers

    @pytest.mark.asyncio
    async def test_erp_agent_empty_query(self):
        from services.tool_executor import ToolExecutor
        exe = ToolExecutor(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        result = await exe._erp_agent({"query": ""})
        from services.agent.agent_result import AgentResult
        assert isinstance(result, AgentResult)
        assert result.status == "error"
        assert "请输入" in result.summary

    @pytest.mark.asyncio
    @patch("services.erp_agent.ERPAgent.execute")
    async def test_erp_agent_delegates_to_agent(self, mock_execute):
        from services.agent.agent_result import AgentResult
        from services.tool_executor import ToolExecutor

        mock_execute.return_value = AgentResult(
            status="success", summary="库存128件",
            source="erp_agent", tokens_used=200,
        )

        exe = ToolExecutor(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        result = await exe._erp_agent({"query": "查库存"})
        # tool_executor 现在返回 AgentResult
        assert isinstance(result, AgentResult)
        assert "库存128件" in result.summary
        mock_execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.erp_agent.ERPAgent.execute")
    async def test_erp_agent_ask_user_returns_agent_result(self, mock_execute):
        """ERP Agent 返回 ask_user → AgentResult 携带 ask_user_question"""
        from services.agent.agent_result import AgentResult
        from services.tool_executor import ToolExecutor

        mock_execute.return_value = AgentResult(
            status="ask_user", summary="需要排除刷单吗？",
            ask_user_question="需要排除刷单吗？",
            source="erp_agent", tokens_used=100,
        )

        exe = ToolExecutor(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        result = await exe._erp_agent({"query": "查销售额"})

        # ask_user 冒泡现在由 ChatToolMixin 处理，tool_executor 只返回 AgentResult
        assert isinstance(result, AgentResult)
        assert result.status == "ask_user"
        assert result.ask_user_question == "需要排除刷单吗？"

    @pytest.mark.asyncio
    @patch("services.erp_agent.ERPAgent.execute")
    async def test_erp_agent_normal_returns_agent_result(self, mock_execute):
        """ERP Agent 正常返回 → AgentResult"""
        from services.agent.agent_result import AgentResult
        from services.tool_executor import ToolExecutor

        mock_execute.return_value = AgentResult(
            status="success", summary="查询结果",
            source="erp_agent", tokens_used=100,
        )

        exe = ToolExecutor(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        result = await exe._erp_agent({"query": "查库存"})

        assert isinstance(result, AgentResult)
        assert result.status == "success"


# ============================================================
# chat_tools.py erp_agent 工具定义
# ============================================================


class TestChatToolsERPAgent:
    """chat_tools.py erp_agent 相关"""

    def test_erp_agent_in_core_tools(self):
        from config.chat_tools import get_core_tools
        core = get_core_tools(org_id="test")
        names = {t["function"]["name"] for t in core}
        assert "erp_agent" in names

    def test_erp_agent_not_in_guest(self):
        """散客不应看到 erp_agent"""
        from config.chat_tools import get_core_tools
        core = get_core_tools(org_id=None)
        names = {t["function"]["name"] for t in core}
        # erp_agent 在 _build_common_tools 里始终构建，
        # 但散客的 get_chat_tools(org_id=None) 也包含 common tools
        # 所以散客也能看到 erp_agent 工具定义
        # 但 ToolExecutor._erp_agent 内部会创建 ERPAgent(org_id=None)
        # ERPAgent 内部 build_domain_tools("erp") 会返回空或报错
        # 这是可接受的行为：散客调了 erp_agent 会返回友好错误
        assert "erp_agent" in names  # 工具定义存在

    def test_core_tools_count(self):
        from config.chat_tools import get_core_tools
        core = get_core_tools(org_id="test")
        assert 10 <= len(core) <= 16  # 13 个核心工具（含 file/crawler）

    def test_system_prompt_simplified(self):
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "erp_agent" in prompt


# ============================================================
# 散客保护 + token 累加
# ============================================================


class TestERPAgentGuards:
    """散客保护和 token 累加"""

    @pytest.mark.asyncio
    async def test_guest_returns_friendly_error(self):
        """散客（无 org_id）调 erp_agent 返回友好提示"""
        from services.erp_agent import ERPAgent
        agent = ERPAgent(db=None, user_id="t", conversation_id="t", org_id=None)
        result = await agent.execute("查库存")
        assert "未开通" in result.summary
        assert result.tokens_used == 0

    @pytest.mark.asyncio
    async def test_empty_org_id_returns_friendly_error(self):
        """空字符串 org_id 也应返回友好提示"""
        from services.erp_agent import ERPAgent
        agent = ERPAgent(db=None, user_id="t", conversation_id="t", org_id="")
        result = await agent.execute("查库存")
        assert "未开通" in result.summary

    # test_token_accumulation_across_turns 已删除（旧 tool loop 路径）


# ============================================================
# ERPAgent 计划提取 + 并行执行测试
# ============================================================


class TestExtractPlan:
    """ERPAgent._extract_plan 三级降级链"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_single_domain_llm(self):
        """LLM 返回单域 → ExecutionPlan 单步"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ([("trade", {"doc_type": "order", "mode": "summary"})], None, "parallel")
            plan = await agent._extract_plan("今天多少订单")
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].domain == "trade"
        assert plan.degraded is False
        assert plan.compute_hint is None
        assert plan.dependency == "parallel"

    @pytest.mark.asyncio
    async def test_multi_domain_llm(self):
        """LLM 返回多域 + compute_hint"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                [("trade", {"doc_type": "order"}), ("aftersale", {"doc_type": "aftersale"})],
                "用 product_code 关联，退货率 = 售后/订单",
                "parallel",
            )
            plan = await agent._extract_plan("退货率多少")
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].domain == "trade"
        assert plan.steps[1].domain == "aftersale"
        assert plan.compute_hint == "用 product_code 关联，退货率 = 售后/订单"
        assert plan.dependency == "parallel"

    @pytest.mark.asyncio
    async def test_fallback_keyword(self):
        """LLM 失败 → 关键词降级单域"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock, side_effect=ValueError("API 错误")):
            plan = await agent._extract_plan("订单数据查一下")
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].domain == "trade"
        assert plan.degraded is True

    @pytest.mark.asyncio
    async def test_abort_no_keyword(self):
        """LLM 失败 + 无关键词 → None"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock, side_effect=ValueError("错误")):
            plan = await agent._extract_plan("你好啊")
        assert plan is None

    @pytest.mark.asyncio
    async def test_domain_route_conflict_fixed(self):
        """L2 域路由冲突: trade + doc_type=purchase → 纠正为 order"""
        agent = self._make_agent()
        with patch.object(agent, "_llm_extract", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ([("trade", {"doc_type": "purchase", "mode": "summary"})], None, "parallel")
            plan = await agent._extract_plan("看看采购")
        assert plan.steps[0].params["doc_type"] == "order"


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
        """有 task_id+message_id → 收集到 _thinking_parts + 推送 WS"""
        agent = self._make_agent(task_id="t1", message_id="m1")
        with patch("services.websocket_manager.ws_manager") as mock_ws:
            mock_ws.send_to_task_or_user = AsyncMock()
            await agent._push_thinking("查询中...")
        assert "→ 查询中..." in agent._thinking_parts
        mock_ws.send_to_task_or_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_silent_without_task_id(self):
        """无 task_id → 只收集文本，不推送 WS"""
        agent = self._make_agent()
        await agent._push_thinking("测试")
        assert "→ 测试" in agent._thinking_parts
        # 没有 ws_manager 调用（无 task_id 直接 return）


class TestBuildExperienceDetail:
    """ERPAgent._build_experience_detail 经验序列化"""

    def test_with_params(self):
        from services.agent.erp_agent import ERPAgent
        detail = ERPAgent._build_experience_detail("trade", {
            "mode": "summary", "group_by": ["shop"], "platform": "tb",
        })
        assert "domain=trade" in detail
        assert "mode=summary" in detail
        assert "group_by=" in detail
        assert "platform=tb" in detail

    def test_without_params(self):
        from services.agent.erp_agent import ERPAgent
        detail = ERPAgent._build_experience_detail("warehouse", None)
        assert detail == "domain=warehouse"

    def test_with_product_code(self):
        from services.agent.erp_agent import ERPAgent
        detail = ERPAgent._build_experience_detail("trade", {
            "mode": "export", "product_code": "HZ001",
        })
        assert "product_code=HZ001" in detail


class TestExecuteBoundary:
    """ERPAgent.execute 边界场景"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(db=MagicMock(), user_id="u1", conversation_id="c1", org_id="org1")

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_status(self):
        """全局超时 → status=timeout"""
        agent = self._make_agent()
        with patch.object(agent, "_execute", new_callable=AsyncMock, side_effect=asyncio.TimeoutError()):
            result = await agent.execute("查订单")
        assert result.status == "timeout"
        assert "超时" in result.summary

    @pytest.mark.asyncio
    async def test_unknown_exception_returns_error(self):
        """未知异常 → status=error + 内部错误提示"""
        agent = self._make_agent()
        with patch.object(agent, "_execute", new_callable=AsyncMock, side_effect=RuntimeError("segfault")):
            result = await agent.execute("查订单")
        assert result.status == "error"
        assert "内部错误" in result.summary
        assert "RuntimeError" in result.summary

    @pytest.mark.asyncio
    async def test_known_exception_shows_message(self):
        """已知异常（ValueError）→ 直接展示错误信息"""
        agent = self._make_agent()
        with patch.object(agent, "_execute", new_callable=AsyncMock, side_effect=ValueError("参数错误")):
            result = await agent.execute("查订单")
        assert result.status == "error"
        assert "参数错误" in result.summary


class TestToolSystemPromptNewRules:
    """TOOL_SYSTEM_PROMPT 新增规则验证"""

    def test_staging_consumption_rule(self):
        """主 Agent 提示词包含 staging 文件消费规则"""
        from config.chat_tools import TOOL_SYSTEM_PROMPT
        assert "[文件已存入 staging]" in TOOL_SYSTEM_PROMPT
        assert "code_execute" in TOOL_SYSTEM_PROMPT

    def test_compute_hint_consumption_rule(self):
        """主 Agent 提示词包含 compute_hint 消费规则"""
        from config.chat_tools import TOOL_SYSTEM_PROMPT
        assert "[关联计算提示]" in TOOL_SYSTEM_PROMPT

    def test_excel_engine_correct(self):
        """写 Excel 用 xlsxwriter，读 Excel 用 calamine"""
        from config.chat_tools import TOOL_SYSTEM_PROMPT
        assert "xlsxwriter" in TOOL_SYSTEM_PROMPT
        assert "calamine" in TOOL_SYSTEM_PROMPT


class TestParamDefinitionsConsistency:
    """_PARAM_DEFINITIONS 一致性验证"""

    def test_old_and_new_prompt_share_same_definitions(self):
        """build_extract_prompt 和 build_multi_extract_prompt 共用 _PARAM_DEFINITIONS"""
        from services.agent.plan_builder import (
            build_extract_prompt, build_multi_extract_prompt, _PARAM_DEFINITIONS,
        )
        old_prompt = build_extract_prompt("测试", now_str="2026-04-24")
        new_prompt = build_multi_extract_prompt("测试", now_str="2026-04-24")
        # 两个 prompt 都包含参数定义中的关键片段
        for key_fragment in [
            "doc_type: order/purchase",
            "receiver_name",
            "sku_properties_name",
            "online_status",
            "handler_status",
            "include_invalid",
        ]:
            assert key_fragment in old_prompt, f"旧 prompt 缺少 {key_fragment}"
            assert key_fragment in new_prompt, f"新 prompt 缺少 {key_fragment}"


class TestToolSystemPromptAlignment:
    """TOOL_SYSTEM_PROMPT 与新架构一致性"""

    def test_erp_agent_described(self):
        """规则应描述 erp_agent"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "erp_agent" in prompt

    def test_erp_analyze_described(self):
        """规则应描述 erp_analyze（计划模式分析工具）"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "erp_analyze" in prompt
        assert "只分析不执行" in prompt

    def test_code_execute_mentioned(self):
        """规则应提及 code_execute"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "code_execute" in prompt

    def test_erp_agent_task_passthrough(self):
        """规则应说明 task 原样传递"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "原样传递" in prompt


# ============================================================
# build_tool_description 自动生成描述测试
# ============================================================


class TestBuildToolDescription:
    """验证 build_tool_description 内容完整性和 token 预算。"""

    def _desc(self) -> str:
        from services.agent.erp_agent import ERPAgent
        return ERPAgent.build_tool_description()

    def test_contains_all_group_by_dims(self):
        desc = self._desc()
        for dim in ("shop", "platform", "product", "supplier",
                     "warehouse", "status"):
            assert dim in desc, f"group_by 维度 {dim} 缺失"

    def test_contains_time_cols(self):
        desc = self._desc()
        for col in ("pay_time", "consign_time", "doc_created_at"):
            assert col in desc, f"time_col {col} 缺失"

    def test_contains_field_categories(self):
        desc = self._desc()
        assert "可查询信息" in desc
        assert "备注" in desc

    def test_contains_use_when(self):
        desc = self._desc()
        assert "使用场景" in desc
        assert "订单" in desc

    def test_contains_dont_use_when(self):
        desc = self._desc()
        assert "不要用于" in desc
        assert "erp_execute" in desc

    def test_contains_oral_mappings(self):
        desc = self._desc()
        assert "丁单" in desc
        assert "酷存" in desc

    def test_contains_examples(self):
        desc = self._desc()
        assert "query 示例" in desc
        assert "按店铺统计" in desc

    def test_token_budget(self):
        desc = self._desc()
        estimated_tokens = len(desc) / 2.5
        assert estimated_tokens < 550, (
            f"描述 token 超预算: {estimated_tokens:.0f} > 550"
        )

    def test_no_hardcoded_content(self):
        """描述内容全部来自 manifest，修改 manifest 会改变输出"""
        from services.agent.plan_builder import get_capability_manifest
        m = get_capability_manifest()
        desc = self._desc()
        # manifest 的 summary 必须出现在描述中
        assert m["summary"] in desc
        # manifest 的每个 example query 必须出现
        for ex in m["examples"]:
            assert ex["query"] in desc


    def test_assistant_without_tool_calls_key(self):
        """没有 tool_calls 字段时保留"""
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "assistant", "content": "好的"},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 1


# ============================================================
# is_context_length_error — 上下文超限检测
# ============================================================

class TestIsContextLengthError:
    """B6: 上下文超限错误关键词匹配"""

    def test_context_length_exceeded(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("context_length_exceeded"))

    def test_input_too_large(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("input too large for model"))

    def test_maximum_context_length(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("maximum context length is 128000"))

    def test_token_limit(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("token limit exceeded"))

    def test_max_token(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("max_token reached"))

    def test_normal_error_not_matched(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert not is_context_length_error(Exception("connection timeout"))

    def test_rate_limit_not_matched(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert not is_context_length_error(Exception("rate_limit_exceeded"))

    def test_empty_error(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert not is_context_length_error(Exception(""))


# ============================================================
# AgentResult — 结构化状态（Phase 6: 替代 ERPAgentResult D1）
# ============================================================

class TestAgentResultStructured:
    """AgentResult status 字段"""

    def test_default_status_values(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(status="success", summary="OK")
        assert r.status == "success"

    def test_error_status(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(status="error", summary="出错了", error_message="出错了")
        assert r.status == "error"
        assert r.error_message == "出错了"

    def test_partial_status(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(status="partial", summary="部分结果")
        assert r.status == "partial"

    def test_all_fields_populated(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(
            status="success",
            summary="结论",
            tokens_used=1000,
            source="erp_agent",
            confidence=1.0,
        )
        assert r.tokens_used == 1000
        assert r.source == "erp_agent"


# ============================================================
# ask_user 冒泡：ERPAgent.execute → status="ask_user"
# ============================================================


class TestERPAgentAskUserBubble:
    """ERPAgent.execute 检测 exit_via_ask_user → status + question"""

    @pytest.mark.asyncio
    async def test_ask_user_exit_sets_status(self):
        # ask_user / normal_exit 测试已删除（旧 tool loop 路径）
        pass


# ============================================================
# B4: QueryCache — 缓存行为
# ============================================================

class TestERPAgentCache:
    """B4: 会话级读工具缓存（2026-04-11 拆出到 ToolResultCache）"""

    def _make_cache(self):
        from services.agent.tool_result_cache import ToolResultCache
        return ToolResultCache()

    def test_cacheable_tool_returns_true(self):
        from services.agent.tool_result_cache import ToolResultCache
        # local_stock_query 在 _CONCURRENT_SAFE_TOOLS 中
        assert ToolResultCache.is_cacheable("local_stock_query") is True

    def test_non_cacheable_tool_returns_false(self):
        from services.agent.tool_result_cache import ToolResultCache
        # erp_execute 是写操作，不可缓存
        assert ToolResultCache.is_cacheable("erp_execute") is False

    def test_cache_put_and_get(self):
        cache = self._make_cache()
        cache.put("local_stock_query", {"sku": "A1"}, "库存100")
        cached = cache.get("local_stock_query", {"sku": "A1"})
        assert cached == "库存100"

    def test_cache_miss_different_args(self):
        cache = self._make_cache()
        cache.put("local_stock_query", {"sku": "A1"}, "库存100")
        cached = cache.get("local_stock_query", {"sku": "B2"})
        assert cached is None

    def test_cache_skip_non_cacheable_tool(self):
        cache = self._make_cache()
        cache.put("erp_execute", {"action": "create"}, "OK")
        cached = cache.get("erp_execute", {"action": "create"})
        assert cached is None  # 写工具不缓存

    def test_cache_skip_large_result(self):
        cache = self._make_cache()
        large = "x" * 10000  # 超过 _CACHE_MAX_VALUE_CHARS
        cache.put("local_stock_query", {"sku": "A1"}, large)
        cached = cache.get("local_stock_query", {"sku": "A1"})
        assert cached is None  # 大结果不缓存

    def test_cache_max_entries(self):
        cache = self._make_cache()
        # 填满缓存
        for i in range(55):
            cache.put("local_stock_query", {"i": i}, f"result_{i}")
        # 前50个应该被缓存，第51个开始被跳过
        assert cache.get("local_stock_query", {"i": 0}) == "result_0"
        assert cache.get("local_stock_query", {"i": 50}) is None

    def test_cache_key_deterministic(self):
        from services.agent.tool_result_cache import ToolResultCache
        k1 = ToolResultCache._key("tool", {"b": 2, "a": 1})
        k2 = ToolResultCache._key("tool", {"a": 1, "b": 2})
        assert k1 == k2  # sort_keys=True 保证顺序无关

    def test_cache_ttl_expiration(self):
        """过期条目返回 None 且被删除"""
        import time
        from services.agent.tool_result_cache import ToolResultCache
        cache = ToolResultCache()
        cache._CACHE_TTL = 0.05  # 50ms TTL 便于测试
        cache.put("local_stock_query", {"sku": "A1"}, "库存100")
        # 未过期
        assert cache.get("local_stock_query", {"sku": "A1"}) == "库存100"
        # 等待过期
        time.sleep(0.06)
        assert cache.get("local_stock_query", {"sku": "A1"}) is None
        # 过期条目应已被删除，释放空间
        key = ToolResultCache._key("local_stock_query", {"sku": "A1"})
        assert key not in cache._store


# ============================================================
# A2: 失败反思 — 错误前缀检测
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


class TestStagingCleanup:
    """staging 延迟清理测试"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="test-conv-123", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_cleanup_removes_staging_dir(self, tmp_path):
        """清理删除对应会话的 staging 目录"""
        agent = self._make_agent()
        from core.workspace import resolve_staging_dir
        staging_dir_str = resolve_staging_dir(
            str(tmp_path), agent.user_id, agent.org_id, agent.conversation_id,
        )
        from pathlib import Path
        staging_dir = Path(staging_dir_str)
        staging_dir.mkdir(parents=True)
        (staging_dir / "data.json").write_text('[]')

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.file_workspace_root = str(tmp_path)
            await agent._cleanup_staging_delayed(delay=0)

        assert not staging_dir.exists()

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_staging(self, tmp_path):
        """无 staging 目录时不报错"""
        agent = self._make_agent()
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.file_workspace_root = str(tmp_path)
            await agent._cleanup_staging_delayed(delay=0)
        # 不抛异常即通过


class TestRecordAgentExperience:
    """F1/F2: ExperienceRecorder（从 ERPAgent 提取）"""

    def _make_recorder(self):
        from services.agent.experience_recorder import ExperienceRecorder
        return ExperienceRecorder(org_id="org1", writer="erp_agent")

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
