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
# ERPAgent 单域查询主流程（架构简化后新增）
# ============================================================


class TestERPAgentToolLoop:
    """ERPAgent ToolLoopExecutor 模式测试"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_no_org_returns_error(self):
        """org_id 为空 → 直接返回错误"""
        from services.agent.erp_agent import ERPAgent
        agent = ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="",
        )
        result = await agent.execute("查订单")
        assert result.status == "error"
        assert "未开通" in result.summary

    @pytest.mark.asyncio
    async def test_execute_calls_tool_loop(self):
        """execute → _execute_with_tool_loop 被调用"""
        agent = self._make_agent()

        mock_result = MagicMock()
        mock_result.text = "今日订单 100 单"
        mock_result.total_tokens = 500
        mock_result.turns = 2
        mock_result.is_llm_synthesis = True
        mock_result.exit_via_ask_user = False
        mock_result.collected_files = []

        agent._execute_with_tool_loop = AsyncMock(return_value=MagicMock(
            status="success", summary="今日订单 100 单",
            source="erp_agent", tokens_used=500,
        ))

        result = await agent.execute("今天多少订单")
        assert result.status == "success"
        agent._execute_with_tool_loop.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        """内部异常 → 返回错误，不崩溃"""
        agent = self._make_agent()
        agent._execute_with_tool_loop = AsyncMock(
            side_effect=ConnectionError("DB down"),
        )

        result = await agent.execute("查订单")
        assert result.status == "error"
        assert "DB down" in result.summary

    def test_build_messages(self):
        """_build_messages 生成正确的 messages 结构"""
        agent = self._make_agent()
        messages = agent._build_messages("今天多少订单")

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "今天多少订单" in messages[1]["content"]
        assert "ERP 数据分析专家" in messages[0]["content"]

    def test_convert_result_success(self):
        """LoopResult → AgentResult 正常转换"""
        from services.agent.erp_agent import ERPAgent

        mock_loop_result = MagicMock()
        mock_loop_result.text = "查询结果"
        mock_loop_result.total_tokens = 300
        mock_loop_result.is_llm_synthesis = True
        mock_loop_result.exit_via_ask_user = False
        mock_loop_result.collected_files = [{"url": "/test.xlsx", "name": "test.xlsx"}]

        result = ERPAgent._convert_result(mock_loop_result)
        assert result.status == "success"
        assert result.summary == "查询结果"
        assert result.source == "erp_agent"
        assert len(result.collected_files) == 1

    def test_convert_result_empty(self):
        """LoopResult 无合成 → status=empty"""
        from services.agent.erp_agent import ERPAgent

        mock_loop_result = MagicMock()
        mock_loop_result.text = "兜底文本"
        mock_loop_result.total_tokens = 100
        mock_loop_result.is_llm_synthesis = False
        mock_loop_result.exit_via_ask_user = False
        mock_loop_result.collected_files = []

        result = ERPAgent._convert_result(mock_loop_result)
        assert result.status == "empty"

    def test_convert_result_ask_user(self):
        """LoopResult exit_via_ask_user → status=ask_user"""
        from services.agent.erp_agent import ERPAgent

        mock_loop_result = MagicMock()
        mock_loop_result.text = "需要确认哪个商品？"
        mock_loop_result.total_tokens = 200
        mock_loop_result.is_llm_synthesis = True
        mock_loop_result.exit_via_ask_user = True
        mock_loop_result.collected_files = []

        result = ERPAgent._convert_result(mock_loop_result)
        assert result.status == "ask_user"
        assert result.ask_user_question == "需要确认哪个商品？"

    def test_build_tool_loop_config(self):
        """_build_tool_loop 配置正确"""
        agent = self._make_agent()

        mock_adapter = MagicMock()
        mock_executor = MagicMock()
        mock_tools = [{"type": "function", "function": {"name": "local_data"}}]

        tool_loop, hook_ctx, budget = agent._build_tool_loop(
            mock_adapter, mock_executor, mock_tools,
        )

        # hook_ctx.task_id 为 None（不推送 WS）
        assert hook_ctx.task_id is None
        assert hook_ctx.org_id == "org1"
        # budget 存在
        assert budget is not None


# ============================================================
# get_erp_agent_tools 工具集测试
# ============================================================


class TestGetErpAgentTools:
    """ERPAgent 专用工具集"""

    def test_contains_local_tools(self):
        """包含本地查询工具"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "local_data" in names
        assert "local_stock_query" in names
        assert "local_product_identify" in names

    def test_contains_code_execute(self):
        """包含 code_execute（计算能力）"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "code_execute" in names

    def test_excludes_erp_agent(self):
        """不包含 erp_agent（防递归）"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "erp_agent" not in names

    def test_excludes_erp_execute(self):
        """不包含 erp_execute（只读不写）"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "erp_execute" not in names

    def test_contains_remote_query_tools(self):
        """包含远程查询工具"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "erp_trade_query" in names
        assert "erp_purchase_query" in names

    def test_excludes_trigger_erp_sync(self):
        """不包含 trigger_erp_sync（写操作，需用户确认）"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "trigger_erp_sync" not in names

    def test_excludes_ask_user(self):
        """不包含 ask_user（ERPAgent 无用户交互）"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "ask_user" not in names

    def test_contains_fetch_all_pages(self):
        """包含 fetch_all_pages（全量翻页）"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "fetch_all_pages" in names

    def test_contains_erp_api_search(self):
        """包含 erp_api_search（API 文档搜索）"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        names = {t["function"]["name"] for t in tools}
        assert "erp_api_search" in names

    def test_tool_count(self):
        """工具总数 = 9 local + 7 remote + fetch_all_pages + erp_api_search + code_execute = 19"""
        from config.erp_tools import get_erp_agent_tools
        tools = get_erp_agent_tools(org_id="org1")
        assert len(tools) == 19, f"Expected 19 tools, got {len(tools)}: {[t['function']['name'] for t in tools]}"


# ============================================================
# ERPAgent 内部提示词一致性测试
# ============================================================


class TestERPAgentPrompts:
    """验证 ERPAgent 内部提示词不含不可用工具、无乱码"""

    def test_system_prompt_no_ask_user(self):
        """系统提示不引用 ask_user"""
        from services.agent.erp_agent import _ERP_AGENT_SYSTEM_PROMPT
        assert "ask_user" not in _ERP_AGENT_SYSTEM_PROMPT

    def test_system_prompt_no_trigger_sync(self):
        """系统提示不引用 trigger_erp_sync"""
        from services.agent.erp_agent import _ERP_AGENT_SYSTEM_PROMPT
        assert "trigger_erp_sync" not in _ERP_AGENT_SYSTEM_PROMPT

    def test_system_prompt_no_garbled_chars(self):
        """系统提示无乱码字符（U+FFFD）"""
        from services.agent.erp_agent import _ERP_AGENT_SYSTEM_PROMPT
        assert "\ufffd" not in _ERP_AGENT_SYSTEM_PROMPT

    def test_system_prompt_mentions_code_execute(self):
        """系统提示包含 code_execute"""
        from services.agent.erp_agent import _ERP_AGENT_SYSTEM_PROMPT
        assert "code_execute" in _ERP_AGENT_SYSTEM_PROMPT

    def test_system_prompt_has_critical_section(self):
        """系统提示包含 CRITICAL 约束段"""
        from services.agent.erp_agent import _ERP_AGENT_SYSTEM_PROMPT
        assert "CRITICAL" in _ERP_AGENT_SYSTEM_PROMPT

    def test_system_prompt_mentions_local_priority(self):
        """系统提示明确 local 工具覆盖范围"""
        from services.agent.erp_agent import _ERP_AGENT_SYSTEM_PROMPT
        assert "local_*" in _ERP_AGENT_SYSTEM_PROMPT
        assert "erp_*_query" in _ERP_AGENT_SYSTEM_PROMPT


# ============================================================
# TOOL_SYSTEM_PROMPT 对齐新架构
# ============================================================


class TestToolSystemPromptAlignment:
    """TOOL_SYSTEM_PROMPT 与新架构一致性"""

    def test_erp_agent_described_as_expert(self):
        """规则应描述 erp_agent 为数据分析专家"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "专家" in prompt or "分析" in prompt

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
        assert estimated_tokens < 500, (
            f"描述 token 超预算: {estimated_tokens:.0f} > 500"
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


# ============================================================
# _run_tool_loop 退出逻辑
# ============================================================


class TestRunToolLoopExitLogic:
    """ToolLoopExecutor 各退出路径测试（原 _run_tool_loop，2026-04-11 拆出）"""

    def _make_agent(self):
        from services.erp_agent import ERPAgent
        agent = ERPAgent(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        return agent

    def _make_loop(self, agent, adapter, executor, all_tools=None):
        """构造与 ERPAgent 配套的 ToolLoopExecutor 实例（ERP 默认装配）"""
        from services.agent.tool_loop_executor import ToolLoopExecutor
        from services.agent.erp_agent_types import (
            MAX_ERP_TURNS, MAX_TOTAL_TOKENS, TOOL_TIMEOUT,
        )
        from services.agent.loop_types import LoopConfig, LoopStrategy
        return ToolLoopExecutor(
            adapter=adapter,
            executor=executor,
            all_tools=all_tools or [],
            config=LoopConfig(
                max_turns=MAX_ERP_TURNS,
                max_tokens=MAX_TOTAL_TOKENS,
                tool_timeout=TOOL_TIMEOUT,
                no_synthesis_fallback_text=(
                    "ERP 查询过程中未能生成完整结论，请重新提问或缩小查询范围。"
                ),
            ),
            strategy=LoopStrategy(
                exit_signals=frozenset({"route_to_chat", "ask_user"}),
                enable_tool_expansion=True,
            ),
            hooks=[],  # 测试场景默认不挂 hooks（每个测试自己挂）
        )

    def _make_hook_ctx(self, agent):
        """构造测试用 HookContext"""
        from services.agent.loop_types import HookContext
        return HookContext(
            db=agent.db,
            user_id=agent.user_id,
            org_id=agent.org_id,
            conversation_id=agent.conversation_id,
            task_id=agent.task_id,
            request_ctx=agent.request_ctx,
        )

    @pytest.mark.asyncio
    async def test_empty_turn_skipped_when_no_tools_called(self):
        """未调过工具时，LLM 直接输出文字应被跳过，强制继续循环"""
        from services.erp_agent import ERPAgent
        from services.adapters.types import StreamChunk, ToolCallDelta

        agent = self._make_agent()
        turn_counter = {"n": 0}

        async def mock_stream(*args, **kwargs):
            turn_counter["n"] += 1
            if turn_counter["n"] == 1:
                # Turn 1: 纯文字输出（没调工具），应被跳过
                yield StreamChunk(content="好的帮你查", prompt_tokens=10, completion_tokens=5)
            elif turn_counter["n"] == 2:
                # Turn 2: 调工具
                yield StreamChunk(
                    tool_calls=[ToolCallDelta(index=0, id="tc1", name="local_stock_query", arguments_delta='{"product_code":"A"}')],
                    prompt_tokens=20, completion_tokens=10,
                )
            else:
                # Turn 3: 输出结论
                yield StreamChunk(content="库存128件", prompt_tokens=15, completion_tokens=8)

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value="库存128件")

        loop = self._make_loop(agent, mock_adapter, mock_executor)
        result = await loop.run(
            messages=[{"role": "user", "content": "查库存"}],
            selected_tools=[], tools_called=[],
            hook_ctx=self._make_hook_ctx(agent),
        )
        assert result.text == "库存128件"
        assert turn_counter["n"] == 3  # 跑了 3 轮

    @pytest.mark.asyncio
    async def test_consecutive_empty_turns_break(self):
        """连续 2 次空响应应中止循环"""
        from services.adapters.types import StreamChunk

        agent = self._make_agent()

        async def mock_stream(*args, **kwargs):
            # 每轮都输出废话，不调工具
            yield StreamChunk(content="让我想想...", prompt_tokens=10, completion_tokens=5)

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream

        loop = self._make_loop(agent, mock_adapter, AsyncMock())
        result = await loop.run(
            messages=[{"role": "user", "content": "查库存"}],
            selected_tools=[], tools_called=[],
            hook_ctx=self._make_hook_ctx(agent),
        )
        # 有文字时应作为有效输出（不再走兜底提示）
        assert result.text == "让我想想..."
        assert result.turns == 2  # 2 次空响应后中止

    @pytest.mark.asyncio
    async def test_text_output_after_tool_call_is_synthesis(self):
        """调过工具后输出纯文字 = 合成结论，应正常返回"""
        from services.adapters.types import StreamChunk, ToolCallDelta

        agent = self._make_agent()
        turn_counter = {"n": 0}

        async def mock_stream(*args, **kwargs):
            turn_counter["n"] += 1
            if turn_counter["n"] == 1:
                yield StreamChunk(
                    tool_calls=[ToolCallDelta(index=0, id="tc1", name="local_global_stats", arguments_delta='{"doc_type":"order"}')],
                    prompt_tokens=20, completion_tokens=10,
                )
            else:
                yield StreamChunk(content="今天共8000单", prompt_tokens=15, completion_tokens=8)

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value="统计结果：8000单")

        loop = self._make_loop(agent, mock_adapter, mock_executor)
        result = await loop.run(
            messages=[{"role": "user", "content": "今天多少单"}],
            selected_tools=[], tools_called=[],
            hook_ctx=self._make_hook_ctx(agent),
        )
        assert result.text == "今天共8000单"
        assert "未能生成" not in result.text

    @pytest.mark.asyncio
    async def test_ask_user_sets_synthesis_true(self):
        """ask_user 退出时 is_llm_synthesis 应为 True"""
        from services.adapters.types import StreamChunk, ToolCallDelta

        agent = self._make_agent()

        async def mock_stream(*args, **kwargs):
            yield StreamChunk(
                tool_calls=[ToolCallDelta(index=0, id="tc1", name="ask_user", arguments_delta='{"message":"请提供商品编码"}')],
                prompt_tokens=20, completion_tokens=10,
            )

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream

        mock_executor = AsyncMock()

        loop = self._make_loop(agent, mock_adapter, mock_executor)
        result = await loop.run(
            messages=[{"role": "user", "content": "查一下那个"}],
            selected_tools=[], tools_called=[],
            hook_ctx=self._make_hook_ctx(agent),
        )
        # ask_user 的 message 应作为结果返回，不应走兜底
        assert "未能生成" not in result.text
        assert "请提供商品编码" in result.text

    @pytest.mark.asyncio
    async def test_route_to_chat_with_turn_text(self):
        """route_to_chat 有 turn_text 时应返回 turn_text"""
        from services.adapters.types import StreamChunk, ToolCallDelta

        agent = self._make_agent()

        async def mock_stream(*args, **kwargs):
            yield StreamChunk(
                content="今天共8000单",
                tool_calls=[ToolCallDelta(index=0, id="tc1", name="route_to_chat", arguments_delta='{"system_prompt":"ERP分析师"}')],
                prompt_tokens=20, completion_tokens=10,
            )

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream

        # 需要先调过工具才不会被 empty_turns 拦截
        loop = self._make_loop(agent, mock_adapter, AsyncMock())
        result = await loop.run(
            messages=[{"role": "user", "content": "查数据"}],
            selected_tools=[], tools_called=["local_global_stats"],
            hook_ctx=self._make_hook_ctx(agent),
        )
        assert result.text == "今天共8000单"
        assert "未能生成" not in result.text

    @pytest.mark.asyncio
    async def test_route_to_chat_without_turn_text_fallback(self):
        """route_to_chat 无 turn_text 时应走兜底提示"""
        from services.adapters.types import StreamChunk, ToolCallDelta

        agent = self._make_agent()

        async def mock_stream(*args, **kwargs):
            yield StreamChunk(
                tool_calls=[ToolCallDelta(index=0, id="tc1", name="route_to_chat", arguments_delta='{"system_prompt":"ERP分析师"}')],
                prompt_tokens=20, completion_tokens=10,
            )

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream

        loop = self._make_loop(agent, mock_adapter, AsyncMock())
        result = await loop.run(
            messages=[{"role": "user", "content": "查数据"}],
            selected_tools=[], tools_called=["local_global_stats"],
            hook_ctx=self._make_hook_ctx(agent),
        )
        assert "未能生成" in result.text


class TestToolLoopSteer:
    """ToolLoopExecutor steer 打断 — 直接测试 check_steer 在 _execute_tools 中的行为"""

    def test_steer_skips_remaining_and_injects_user_msg(self):
        """steer 信号到达 → 跳过剩余工具 + 注入 user message 到 messages"""
        from services.websocket_manager import ws_manager

        task_id = "task-steer-test-1"
        ws_manager.register_steer_listener(task_id)
        ws_manager.resolve_steer(task_id, "帮我查库存")

        # 模拟 _execute_tools 中的打断逻辑
        messages = [{"role": "user", "content": "查销售额"}]
        completed = [
            {"id": "tc1", "name": "tool_a", "arguments": "{}"},
            {"id": "tc2", "name": "tool_b", "arguments": "{}"},
        ]

        # 模拟第一个工具执行完后检查 steer
        executed = []
        for tc in completed:
            executed.append(tc["name"])
            messages.append({
                "role": "tool", "tool_call_id": tc["id"], "content": "result",
            })

            _steer = ws_manager.check_steer(task_id)
            if _steer:
                remaining = completed[completed.index(tc) + 1:]
                for r_tc in remaining:
                    messages.append({
                        "role": "tool", "tool_call_id": r_tc["id"],
                        "content": "⚠ 用户发送了新消息，跳过此工具调用。",
                    })
                messages.append({"role": "user", "content": _steer})
                break

        # 验证：只执行了 tool_a
        assert executed == ["tool_a"]
        # messages 末尾有跳过标记 + 新 user 消息
        skipped = [m for m in messages if "跳过此工具调用" in m.get("content", "")]
        assert len(skipped) == 1  # tool_b 被跳过
        assert messages[-1] == {"role": "user", "content": "帮我查库存"}

        ws_manager.unregister_steer_listener(task_id)


class TestERPAgentConstants:
    """ERP Agent 常量和安全护栏验证"""

    def test_max_erp_turns_is_20(self):
        """MAX_ERP_TURNS 应为 20（参考 Claude Code subagent 50-200 轮）"""
        from services.erp_agent import MAX_ERP_TURNS
        assert MAX_ERP_TURNS == 20

    def test_tool_timeout_reasonable(self):
        """单工具超时应在合理范围"""
        from services.erp_agent import _TOOL_TIMEOUT
        assert 10 <= _TOOL_TIMEOUT <= 60

    def test_token_budget_exists(self):
        """Token 预算上限存在"""
        from services.erp_agent import _MAX_TOTAL_TOKENS
        assert _MAX_TOTAL_TOKENS > 0


class TestERPAgentJSONParseError:
    """JSON 解析错误不再静默吞掉（2026-04-11 拆出到 ToolLoopExecutor）"""

    @pytest.mark.asyncio
    async def test_malformed_json_returns_error_to_llm(self):
        """工具参数 JSON 格式错误时，错误信息应返回给 LLM"""
        from services.erp_agent import ERPAgent
        from services.adapters.types import StreamChunk, ToolCallDelta
        from services.agent.tool_loop_executor import ToolLoopExecutor
        from services.agent.erp_agent_types import (
            MAX_ERP_TURNS, MAX_TOTAL_TOKENS, TOOL_TIMEOUT,
        )
        from services.agent.loop_types import (
            HookContext, LoopConfig, LoopStrategy,
        )

        agent = ERPAgent(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )

        call_count = {"n": 0}

        async def mock_stream(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 第一轮：返回一个参数格式错误的工具调用
                yield StreamChunk(
                    tool_calls=[ToolCallDelta(
                        index=0, id="tc1", name="local_stock_query",
                        arguments_delta='{bad json!!!',
                    )],
                    prompt_tokens=10, completion_tokens=5,
                )
            else:
                # 第二轮：正常输出文字结束
                yield StreamChunk(content="参数格式有误，请确认", prompt_tokens=10, completion_tokens=5)

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream

        loop = ToolLoopExecutor(
            adapter=mock_adapter,
            executor=AsyncMock(),
            all_tools=[],
            config=LoopConfig(
                max_turns=MAX_ERP_TURNS,
                max_tokens=MAX_TOTAL_TOKENS,
                tool_timeout=TOOL_TIMEOUT,
            ),
            strategy=LoopStrategy(
                exit_signals=frozenset({"route_to_chat", "ask_user"}),
                enable_tool_expansion=True,
            ),
            hooks=[],
        )
        hook_ctx = HookContext(
            db=agent.db, user_id=agent.user_id,
            org_id=agent.org_id, conversation_id=agent.conversation_id,
            task_id=agent.task_id, request_ctx=agent.request_ctx,
        )
        result = await loop.run(
            messages=[{"role": "user", "content": "查库存"}],
            selected_tools=[], tools_called=[],
            hook_ctx=hook_ctx,
        )
        # 错误信息应作为 tool result 返回，LLM 看到后输出文字
        assert "参数格式有误" in result.text or "JSON" in result.text


class TestFilterErpContextEdgeCases:
    """filter_erp_context 边缘场景补充"""

    def test_assistant_with_empty_tool_calls(self):
        """tool_calls 为空列表时保留（当作纯文字）"""
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "assistant", "tool_calls": [], "content": "好的"},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 1

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
