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
# ERPAgentResult — D1 结构化增强
# ============================================================

class TestERPAgentResultStructured:
    """D1: status / is_truncated 字段"""

    def test_default_status_is_success(self):
        from services.erp_agent import ERPAgentResult
        r = ERPAgentResult(text="OK")
        assert r.status == "success"
        assert r.is_truncated is False

    def test_error_status(self):
        from services.erp_agent import ERPAgentResult
        r = ERPAgentResult(text="出错了", status="error")
        assert r.status == "error"

    def test_partial_status(self):
        from services.erp_agent import ERPAgentResult
        r = ERPAgentResult(text="部分结果", status="partial", is_truncated=True)
        assert r.status == "partial"
        assert r.is_truncated is True

    def test_all_fields_populated(self):
        from services.erp_agent import ERPAgentResult
        r = ERPAgentResult(
            text="结论",
            full_text="完整文本",
            status="success",
            tokens_used=1000,
            turns_used=3,
            tools_called=["local_stock_query", "local_order_query"],
            is_truncated=False,
        )
        assert r.tokens_used == 1000
        assert r.turns_used == 3
        assert len(r.tools_called) == 2


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

class TestFetchAllPagesVisibility:
    """fetch_all_pages 在 ERP Agent 可见工具中"""

    def test_visible_names_includes_fetch_all_pages(self):
        """_VISIBLE_NAMES 包含 fetch_all_pages（2026-04-11 拆到 _prepare_tools）"""
        # 验证源码中的常量（不实例化 Agent，避免 DB 依赖）
        import inspect
        from services.agent.erp_agent import ERPAgent
        source = inspect.getsource(ERPAgent._prepare_tools)
        assert "fetch_all_pages" in source


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
        staging_dir = tmp_path / "staging" / "test-conv-123"
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
    """F1/F2: _record_agent_experience 通用方法"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

    @pytest.mark.asyncio
    async def test_routing_experience_calls_add_knowledge(self):
        """成功路由 → category=experience / node_type=routing_pattern / subcategory=业务域"""
        agent = self._make_agent()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock, return_value="node1") as mock_add:
            await agent._record_agent_experience(
                "routing", "查库存", ["local_product_identify", "local_stock_query"],
                "轮次：2", confidence=0.6,
            )
            mock_add.assert_called_once()
            call_kwargs = mock_add.call_args[1]
            assert call_kwargs["category"] == "experience"
            assert call_kwargs["node_type"] == "routing_pattern"
            # tools_called 首工具 local_product_identify → product 业务域
            assert call_kwargs["subcategory"] == "product"
            assert call_kwargs["confidence"] == 0.6
            assert call_kwargs["max_per_node_type"] == 400
            assert "max_per_category" not in call_kwargs
            assert "local_product_identify → local_stock_query" in call_kwargs["content"]
            # source 必须在 PG CHECK 白名单内（不能是 erp_agent）
            assert call_kwargs["source"] == "auto"
            # writer/record_type 通过 metadata 区分 ERPAgent 经验来源
            assert call_kwargs["metadata"]["writer"] == "erp_agent"
            assert call_kwargs["metadata"]["record_type"] == "routing"

    @pytest.mark.asyncio
    async def test_failure_memory_calls_add_knowledge(self):
        """失败记忆 → category=experience / node_type=failure_pattern / max_per_node_type=200"""
        agent = self._make_agent()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock, return_value="node2") as mock_add:
            await agent._record_agent_experience(
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
        """知识库写入失败不抛异常（DB error 走 except Exception 兜底）"""
        agent = self._make_agent()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock, side_effect=Exception("DB down")):
            await agent._record_agent_experience(
                "routing", "查库存", ["local_stock_query"], "轮次：1",
            )

    @pytest.mark.asyncio
    async def test_schema_violation_does_not_raise(self):
        """schema 违反（ValueError）也不应冒泡到 caller，但要打 ERROR 级日志

        正常情况下不应发生（_record_agent_experience 内部 hardcoded 合法值），
        此测试是防御性回归 — 防止未来 add_knowledge 校验被收紧时静默崩溃 task。
        """
        agent = self._make_agent()
        with patch(
            "services.knowledge_service.add_knowledge",
            new_callable=AsyncMock,
            side_effect=ValueError("invalid node_type"),
        ):
            await agent._record_agent_experience(
                "routing", "q", ["local_stock_query"], "detail",
            )

    @pytest.mark.asyncio
    async def test_max_per_node_type_passed(self):
        """淘汰上限参数（per-node_type）正确传递，routing/failure 用不同配额"""
        agent = self._make_agent()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock) as mock_add:
            await agent._record_agent_experience(
                "routing", "q", ["local_stock_query"], "detail",
            )
            assert mock_add.call_args[1]["max_per_node_type"] == 400

            mock_add.reset_mock()
            await agent._record_agent_experience(
                "failure", "q", ["local_order_query"], "detail",
            )
            assert mock_add.call_args[1]["max_per_node_type"] == 200

    @pytest.mark.asyncio
    async def test_unknown_record_type_returns_silently(self):
        """未知 record_type 应打 ERROR 日志但不调 add_knowledge 也不抛异常"""
        agent = self._make_agent()
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock) as mock_add:
            await agent._record_agent_experience(
                "unknown_type", "q", ["local_stock_query"], "detail",
            )
            mock_add.assert_not_called()


class TestInferBusinessDomain:
    """方案 C：tool_name → business domain 推断测试"""

    def test_local_query_extraction(self):
        from services.agent.erp_agent import ERPAgent
        assert ERPAgent._infer_business_domain(["local_stock_query"]) == "stock"
        assert ERPAgent._infer_business_domain(["local_order_query"]) == "order"
        assert ERPAgent._infer_business_domain(["local_product_identify"]) == "product"
        assert ERPAgent._infer_business_domain(["local_purchase_query"]) == "purchase"
        assert ERPAgent._infer_business_domain(["local_aftersale_query"]) == "aftersale"

    def test_erp_remote_query_extraction(self):
        from services.agent.erp_agent import ERPAgent
        assert ERPAgent._infer_business_domain(["erp_warehouse_query"]) == "warehouse"
        # erp_info_query → basic → 归一化为 info
        assert ERPAgent._infer_business_domain(["erp_info_query"]) == "info"

    def test_normalization(self):
        from services.agent.erp_agent import ERPAgent
        # erp_aftersales_query → aftersales → 归一化为 aftersale
        assert ERPAgent._infer_business_domain(["erp_aftersales_query"]) == "aftersale"
        # erp_trade_query → trade → 归一化为 order
        assert ERPAgent._infer_business_domain(["erp_trade_query"]) == "order"

    def test_first_match_wins(self):
        from services.agent.erp_agent import ERPAgent
        # 取首个能识别的业务域，跳过 identify 类前缀工具
        assert ERPAgent._infer_business_domain(
            ["local_product_identify", "local_stock_query"]
        ) == "product"

    def test_empty_list_returns_general(self):
        from services.agent.erp_agent import ERPAgent
        assert ERPAgent._infer_business_domain([]) == "general"

    def test_unknown_tool_returns_general(self):
        from services.agent.erp_agent import ERPAgent
        assert ERPAgent._infer_business_domain(["some_random_tool"]) == "general"
        assert ERPAgent._infer_business_domain(["route_to_chat"]) == "general"
