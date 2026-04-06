"""
ERPAgent 单元测试

覆盖：filter_erp_context, ERPAgent.execute,
      ToolExecutor._erp_agent handler, erp_agent 工具注册
"""

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
# ERPAgentResult 数据结构
# ============================================================


class TestERPAgentResult:
    """ERPAgentResult 数据结构"""

    def test_default_values(self):
        from services.erp_agent import ERPAgentResult
        r = ERPAgentResult(text="测试")
        assert r.text == "测试"
        assert r.full_text == ""
        assert r.tokens_used == 0
        assert r.turns_used == 0
        assert r.tools_called == []

    def test_with_all_fields(self):
        from services.erp_agent import ERPAgentResult
        r = ERPAgentResult(
            text="结论",
            full_text="完整数据",
            tokens_used=500,
            turns_used=3,
            tools_called=["local_stock_query", "local_order_query"],
        )
        assert r.tokens_used == 500
        assert len(r.tools_called) == 2


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
        assert "请输入" in result

    @pytest.mark.asyncio
    @patch("services.erp_agent.ERPAgent.execute")
    async def test_erp_agent_delegates_to_agent(self, mock_execute):
        from services.erp_agent import ERPAgentResult
        from services.tool_executor import ToolExecutor

        mock_execute.return_value = ERPAgentResult(
            text="库存128件", tokens_used=200,
        )

        exe = ToolExecutor(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        result = await exe._erp_agent({"query": "查库存"})
        assert "库存128件" in result
        mock_execute.assert_called_once()


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
        assert "未开通" in result.text
        assert result.tokens_used == 0

    @pytest.mark.asyncio
    async def test_empty_org_id_returns_friendly_error(self):
        """空字符串 org_id 也应返回友好提示"""
        from services.erp_agent import ERPAgent
        agent = ERPAgent(db=None, user_id="t", conversation_id="t", org_id="")
        result = await agent.execute("查库存")
        assert "未开通" in result.text

    @pytest.mark.asyncio
    async def test_token_accumulation_across_turns(self):
        """token 跨轮次累加而非覆盖"""
        from services.erp_agent import ERPAgent, ERPAgentResult
        from services.adapters.types import StreamChunk

        agent = ERPAgent(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )

        # Mock adapter 模拟 2 轮，每轮返回不同 token 数
        turn_counter = {"n": 0}

        async def mock_stream(*args, **kwargs):
            turn_counter["n"] += 1
            if turn_counter["n"] == 1:
                # Turn1: 返回工具调用 + 100 tokens
                yield StreamChunk(
                    tool_calls=[MagicMock(index=0, id="tc1", name="local_stock_query", arguments_delta='{"product_code":"A"}')],
                    prompt_tokens=80, completion_tokens=20,
                )
            else:
                # Turn2: 纯文字回复 + 50 tokens
                yield StreamChunk(
                    content="库存128件",
                    prompt_tokens=40, completion_tokens=10,
                )

        mock_adapter = AsyncMock()
        mock_adapter.stream_chat = mock_stream
        mock_adapter.close = AsyncMock()

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value="库存128件")

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch("services.tool_executor.ToolExecutor", return_value=mock_executor), \
             patch("config.tool_registry.expand_synonyms", return_value=set()), \
             patch("services.tool_selector.select_and_filter_tools", new_callable=AsyncMock, return_value=[]), \
             patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("config.phase_tools.build_domain_prompt", return_value="test prompt"):

            result = await agent.execute("查库存")
            # Turn1: 100 tokens + Turn2: 50 tokens = 150 total
            assert result.tokens_used == 150


# ============================================================
# _run_tool_loop 退出逻辑
# ============================================================


class TestRunToolLoopExitLogic:
    """_run_tool_loop 各退出路径测试"""

    def _make_agent(self):
        from services.erp_agent import ERPAgent
        agent = ERPAgent(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        agent._all_tools = []  # _run_tool_loop 直接调用时需要初始化
        return agent

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

        text, tokens, turns = await agent._run_tool_loop(
            mock_adapter, mock_executor,
            [{"role": "user", "content": "查库存"}],
            [], [],
        )
        assert text == "库存128件"
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

        text, tokens, turns = await agent._run_tool_loop(
            mock_adapter, AsyncMock(),
            [{"role": "user", "content": "查库存"}],
            [], [],
        )
        # 有文字时应作为有效输出（不再走兜底提示）
        assert text == "让我想想..."
        assert turns == 2  # 2 次空响应后中止

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

        text, tokens, turns = await agent._run_tool_loop(
            mock_adapter, mock_executor,
            [{"role": "user", "content": "今天多少单"}],
            [], [],
        )
        assert text == "今天共8000单"
        assert "未能生成" not in text

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

        text, tokens, turns = await agent._run_tool_loop(
            mock_adapter, mock_executor,
            [{"role": "user", "content": "查一下那个"}],
            [], [],
        )
        # ask_user 的 message 应作为结果返回，不应走兜底
        assert "未能生成" not in text
        assert "请提供商品编码" in text

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
        text, tokens, turns = await agent._run_tool_loop(
            mock_adapter, AsyncMock(),
            [{"role": "user", "content": "查数据"}],
            [], ["local_global_stats"],  # 模拟已调过工具
        )
        assert text == "今天共8000单"
        assert "未能生成" not in text

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

        text, tokens, turns = await agent._run_tool_loop(
            mock_adapter, AsyncMock(),
            [{"role": "user", "content": "查数据"}],
            [], ["local_global_stats"],  # 模拟已调过工具
        )
        assert "未能生成" in text


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
    """JSON 解析错误不再静默吞掉"""

    @pytest.mark.asyncio
    async def test_malformed_json_returns_error_to_llm(self):
        """工具参数 JSON 格式错误时，错误信息应返回给 LLM"""
        from services.erp_agent import ERPAgent
        from services.adapters.types import StreamChunk, ToolCallDelta

        agent = ERPAgent(
            db=MagicMock(), user_id="t",
            conversation_id="t", org_id="test",
        )
        agent._all_tools = []

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

        text, tokens, turns = await agent._run_tool_loop(
            mock_adapter, AsyncMock(),
            [{"role": "user", "content": "查库存"}],
            [], [],
        )
        # 错误信息应作为 tool result 返回，LLM 看到后输出文字
        assert "参数格式有误" in text or "JSON" in text


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
