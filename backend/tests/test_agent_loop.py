"""
Agent Loop + AgentGuardrails 单元测试

覆盖：护栏（循环检测/token预算）、工具分发、结果构建、多轮循环
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_types import AgentResult, AgentGuardrails, PendingAsyncTool
from services.agent_loop import AgentLoop
from services.agent_result_builder import (
    build_chat_result,
    build_terminal_result,
    build_ask_user_result,
    build_search_result,
    build_async_result,
    build_graceful_timeout,
)


# ============================================================
# Helpers
# ============================================================

def _text_content(text: str):
    return [TextPart(text=text)]


def _make_brain_response(
    tool_calls: list | None = None,
    content: str | None = None,
    usage: dict | None = None,
) -> dict:
    """构造模拟的大脑 API 响应"""
    message = {}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if content:
        message["content"] = content
    return {
        "choices": [{"message": message}],
        "usage": usage or {"total_tokens": 100},
    }


def _make_tool_call(name: str, arguments: dict, tc_id: str = "tc_1") -> dict:
    return {
        "id": tc_id,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _make_loop() -> AgentLoop:
    """创建 AgentLoop 实例（mock db）"""
    return AgentLoop(db=MagicMock(), user_id="u1", conversation_id="c1")


# ============================================================
# TestAgentGuardrails
# ============================================================


class TestAgentGuardrails:

    def test_default_values(self):
        g = AgentGuardrails()
        assert g.max_turns == 3
        assert g.max_total_tokens == 3000
        assert g.tokens_used == 0

    def test_add_tokens(self):
        g = AgentGuardrails()
        g.add_tokens(100)
        g.add_tokens(200)
        assert g.tokens_used == 300

    def test_should_abort_below_budget(self):
        g = AgentGuardrails(max_total_tokens=1000)
        g.add_tokens(999)
        assert g.should_abort() is False

    def test_should_abort_at_budget(self):
        g = AgentGuardrails(max_total_tokens=1000)
        g.add_tokens(1000)
        assert g.should_abort() is True

    def test_should_abort_above_budget(self):
        g = AgentGuardrails(max_total_tokens=1000)
        g.add_tokens(1500)
        assert g.should_abort() is True

    def test_detect_loop_three_identical(self):
        """连续3次相同调用→True"""
        g = AgentGuardrails()
        args = {"search_query": "test"}
        assert g.detect_loop("web_search", args) is False
        assert g.detect_loop("web_search", args) is False
        assert g.detect_loop("web_search", args) is True

    def test_detect_loop_different_calls(self):
        """3次不同调用→False"""
        g = AgentGuardrails()
        assert g.detect_loop("web_search", {"search_query": "a"}) is False
        assert g.detect_loop("web_search", {"search_query": "b"}) is False
        assert g.detect_loop("web_search", {"search_query": "c"}) is False

    def test_detect_loop_two_same_one_diff(self):
        """2同1异→False"""
        g = AgentGuardrails()
        args = {"search_query": "same"}
        assert g.detect_loop("web_search", args) is False
        assert g.detect_loop("web_search", args) is False
        assert g.detect_loop("web_search", {"search_query": "diff"}) is False


# ============================================================
# TestProcessToolCall
# ============================================================


class TestProcessToolCall:

    @pytest.mark.asyncio
    async def test_invalid_tool_returns_error_result(self):
        """无效工具名→error result 不终止循环"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("hallucinated_tool", {})
        tool_results = []
        result = await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            pending_async=[],
        )
        assert result is None  # 不终止循环
        assert len(tool_results) == 1
        assert tool_results[0]["is_error"] is True

    @pytest.mark.asyncio
    async def test_loop_detected_returns_graceful_timeout(self):
        """循环检测→graceful timeout"""
        loop = _make_loop()
        loop._settings = MagicMock()
        guardrails = AgentGuardrails()
        args = {"search_query": "same query"}
        # 先填充2次
        guardrails.detect_loop("web_search", args)
        guardrails.detect_loop("web_search", args)

        tc = _make_tool_call("web_search", args)
        result = await loop._process_tool_call(
            tc, turn=2,
            guardrails=guardrails,
            tool_results=[],
            accumulated_context=["prev context"],
            pending_async=[],
        )
        assert result is not None
        assert isinstance(result, AgentResult)

    @pytest.mark.asyncio
    async def test_ask_user_returns_direct_reply(self):
        """ask_user→direct_reply AgentResult"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("ask_user", {
            "message": "请提供更多信息",
            "reason": "need_info",
        })
        result = await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=[],
            accumulated_context=[],
            pending_async=[],
        )
        assert result is not None
        assert result.direct_reply == "请提供更多信息"
        assert result.tool_params["_ask_reason"] == "need_info"

    @pytest.mark.asyncio
    async def test_text_chat_returns_terminal_result(self):
        """text_chat→终端 AgentResult"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("text_chat", {
            "system_prompt": "你是翻译专家",
            "model": "gemini-3-pro",
        })
        result = await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=[],
            accumulated_context=[],
            pending_async=[],
        )
        assert result is not None
        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt == "你是翻译专家"
        assert result.model == "gemini-3-pro"

    @pytest.mark.asyncio
    @patch("config.smart_model_config.SMART_CONFIG", {
        "web_search": {"models": [{"id": "gemini-3-pro", "priority": 1}]},
    })
    async def test_web_search_returns_terminal_result(self):
        """web_search→终端工具，返回 AgentResult（不走 executor）"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("web_search", {"search_query": "iPhone"})
        result = await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=[],
            accumulated_context=["前置上下文"],
            pending_async=[],
        )
        assert result is not None  # 终端工具，立即返回
        assert isinstance(result, AgentResult)
        assert result.generation_type == GenerationType.CHAT
        assert result.model == "gemini-3-pro"
        assert result.tool_params["_needs_google_search"] is True
        assert result.search_context == "前置上下文"

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock)
    async def test_sync_tool_executor_error_returns_error_result(self, mock_notify):
        """同步工具 executor 异常→error result"""
        loop = _make_loop()
        loop._settings = MagicMock()
        loop.executor.execute = AsyncMock(side_effect=Exception("timeout"))
        tc = _make_tool_call("get_conversation_context", {"max_messages": 10})
        tool_results = []
        result = await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            pending_async=[],
        )
        assert result is None
        assert tool_results[0]["is_error"] is True
        assert "timeout" in tool_results[0]["content"]

    @pytest.mark.asyncio
    async def test_generate_image_adds_pending_async(self):
        """generate_image→pending_async"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("generate_image", {
            "prompt": "a cat", "model": "flux-kontext"
        })
        pending = []
        result = await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=[],
            accumulated_context=[],
            pending_async=pending,
        )
        assert result is None
        assert len(pending) == 1
        assert pending[0].tool_name == "generate_image"
        assert pending[0].arguments["prompt"] == "a cat"

    @pytest.mark.asyncio
    async def test_malformed_arguments_parsed_as_empty(self):
        """畸形 arguments JSON→解析为 {}"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = {
            "id": "tc_1",
            "function": {"name": "text_chat", "arguments": "not json!!!"},
        }
        tool_results = []
        # text_chat 需要 system_prompt+model 必填，空 args 验证失败→error result
        result = await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            pending_async=[],
        )
        # validate_tool_call 会因缺少 required 字段返回 False
        assert result is None
        assert tool_results[0]["is_error"] is True


# ============================================================
# TestAgentLoopRun — 集成测试（mock _call_brain）
# ============================================================


class TestAgentLoopRun:

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_brain_no_tool_calls_returns_chat(self, mock_brain, mock_prompt):
        """大脑无 tool_calls→chat result"""
        mock_prompt.return_value = "system"
        mock_brain.return_value = _make_brain_response(content="直接回复")

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            loop = _make_loop()
            result = await loop.run(_text_content("你好"))

        assert result.generation_type == GenerationType.CHAT
        assert result.direct_reply == "直接回复"

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_terminal_tool_returns_immediately(self, mock_brain, mock_prompt):
        """终端工具→立即返回"""
        mock_prompt.return_value = "system"
        mock_brain.return_value = _make_brain_response(
            tool_calls=[_make_tool_call("text_chat", {
                "system_prompt": "你是助手",
                "model": "gemini-3-pro",
            })],
        )

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            loop = _make_loop()
            result = await loop.run(_text_content("翻译"))

        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt == "你是助手"
        assert result.model == "gemini-3-pro"
        mock_brain.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_async_tool_returns_async_result(self, mock_brain, mock_prompt):
        """异步工具→async result (IMAGE)"""
        mock_prompt.return_value = "system"
        mock_brain.return_value = _make_brain_response(
            tool_calls=[_make_tool_call("generate_image", {
                "prompt": "a sunset", "model": "flux-kontext",
            })],
        )

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            loop = _make_loop()
            result = await loop.run(_text_content("画一幅日落"))

        assert result.generation_type == GenerationType.IMAGE
        assert result.tool_params["prompt"] == "a sunset"
        assert result.render_hints is not None

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_max_turns_graceful_timeout(self, mock_brain, mock_prompt, _):
        """超出 max_turns→graceful timeout"""
        mock_prompt.return_value = "system"
        # 每次都返回 get_conversation_context（不同参数避免循环检测）
        call_count = 0

        async def brain_side_effect(messages):
            nonlocal call_count
            call_count += 1
            return _make_brain_response(
                tool_calls=[_make_tool_call(
                    "get_conversation_context",
                    {"max_messages": call_count * 10},
                )],
            )

        mock_brain.side_effect = brain_side_effect

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=2,
                agent_loop_max_tokens=99999,
            )
            loop = _make_loop()
            loop.executor.execute = AsyncMock(return_value="context result")
            result = await loop.run(_text_content("搜索"))

        assert result.turns_used == 2
        assert call_count == 2

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_token_budget_abort(self, mock_brain, mock_prompt):
        """token 超预算→abort"""
        mock_prompt.return_value = "system"
        # 第一次调用就超预算
        mock_brain.return_value = _make_brain_response(
            tool_calls=[_make_tool_call("web_search", {"search_query": "test"})],
            usage={"total_tokens": 5000},
        )

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=5,
                agent_loop_max_tokens=1000,  # 预算只有 1000
            )
            loop = _make_loop()
            loop.executor.execute = AsyncMock(return_value="result")

            with patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock):
                result = await loop.run(_text_content("test"))

        # 第二轮检查 should_abort → True → graceful timeout
        assert result.total_tokens == 5000

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_multi_turn_sync_chain(self, mock_brain, mock_prompt, _):
        """多轮同步链：turn1 get_conversation_context → turn2 text_chat"""
        mock_prompt.return_value = "system"
        responses = [
            _make_brain_response(
                tool_calls=[_make_tool_call(
                    "get_conversation_context", {"max_messages": 10},
                )],
            ),
            _make_brain_response(
                tool_calls=[_make_tool_call("text_chat", {
                    "system_prompt": "数码专家",
                    "model": "gemini-3-pro",
                })],
            ),
        ]
        mock_brain.side_effect = responses

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=5,
                agent_loop_max_tokens=99999,
            )
            loop = _make_loop()
            loop.executor.execute = AsyncMock(return_value="历史对话上下文")
            result = await loop.run(_text_content("iPhone 多少钱"))

        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt == "数码专家"
        assert result.search_context == "历史对话上下文"
        assert mock_brain.await_count == 2

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_empty_choices_returns_chat(self, mock_brain, mock_prompt):
        """空 choices→chat result"""
        mock_prompt.return_value = "system"
        mock_brain.return_value = {
            "choices": [],
            "usage": {"total_tokens": 50},
        }

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            loop = _make_loop()
            result = await loop.run(_text_content("hi"))

        assert result.generation_type == GenerationType.CHAT


# ============================================================
# TestBuildResults
# ============================================================


class TestBuildResults:
    """测试提取到 agent_result_builder 的构建函数"""

    def test_chat_result_with_context(self):
        result = build_chat_result(
            "回复", ["ctx1", "ctx2"], turns=2, tokens=500,
        )
        assert result.generation_type == GenerationType.CHAT
        assert result.search_context == "ctx1\nctx2"
        assert result.direct_reply == "回复"

    def test_chat_result_no_context(self):
        result = build_chat_result(
            "", [], turns=1, tokens=100,
        )
        assert result.search_context is None
        assert result.direct_reply is None  # empty string → None

    def test_async_result_image(self):
        pending = [PendingAsyncTool(
            tool_name="generate_image",
            arguments={"prompt": "cat", "model": "flux"},
        )]
        result = build_async_result(pending, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.IMAGE
        assert result.render_hints == {
            "placeholder_text": "图片生成中",
            "component": "image_grid",
        }

    def test_async_result_video(self):
        pending = [PendingAsyncTool(
            tool_name="generate_video",
            arguments={"prompt": "waves", "model": "vidu"},
        )]
        result = build_async_result(pending, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.VIDEO
        assert result.render_hints["placeholder_text"] == "视频生成中"

    def test_async_result_batch_image(self):
        prompts = [{"prompt": "a"}, {"prompt": "b"}]
        pending = [PendingAsyncTool(
            tool_name="batch_generate_image",
            arguments={"prompts": prompts, "model": "flux"},
        )]
        result = build_async_result(pending, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.IMAGE
        assert result.batch_prompts == prompts

    def test_async_result_empty_pending(self):
        """空 pending→fallback 到 chat"""
        result = build_async_result([], ["ctx"], turns=1, tokens=100)
        assert result.generation_type == GenerationType.CHAT

    def test_graceful_timeout_with_pending(self):
        """graceful timeout + pending async→异步结果"""
        pending = [PendingAsyncTool(
            tool_name="generate_image",
            arguments={"prompt": "cat", "model": "flux"},
        )]
        result = build_graceful_timeout(pending, [], turns=3, tokens=3000)
        assert result.generation_type == GenerationType.IMAGE

    def test_graceful_timeout_with_context(self):
        """graceful timeout + context→chat with context"""
        result = build_graceful_timeout([], ["搜索结果"], turns=3, tokens=3000)
        assert result.generation_type == GenerationType.CHAT
        assert result.search_context == "搜索结果"

    def test_graceful_timeout_empty(self):
        """graceful timeout + 全空→DEFAULT_CHAT_MODEL"""
        result = build_graceful_timeout([], [], turns=3, tokens=3000)
        assert result.generation_type == GenerationType.CHAT
        assert result.model != ""  # 应该有默认模型

    def test_terminal_text_chat(self):
        result = build_terminal_result(
            "text_chat",
            {"system_prompt": "翻译", "model": "gpt-4"},
            ["ctx"],
            [],
            turns=1,
            tokens=100,
        )
        assert result.system_prompt == "翻译"
        assert result.model == "gpt-4"
        assert result.search_context == "ctx"

    def test_terminal_finish_with_pending(self):
        """finish + pending→异步结果"""
        pending = [PendingAsyncTool(
            tool_name="generate_video",
            arguments={"prompt": "waves", "model": "vidu"},
        )]
        result = build_terminal_result(
            "finish", {}, [], pending, turns=2, tokens=200,
        )
        assert result.generation_type == GenerationType.VIDEO

    def test_ask_user_result(self):
        result = build_ask_user_result(
            {"message": "需要更多信息", "reason": "need_info"},
            ["ctx"], [], turns=1, tokens=100,
            conversation_id="c1",
        )
        assert result.direct_reply == "需要更多信息"
        assert result.tool_params["_ask_reason"] == "need_info"
        assert result.search_context == "ctx"

    @patch("config.smart_model_config.SMART_CONFIG", {
        "web_search": {"models": [{"id": "gemini-3-pro", "priority": 1}]},
    })
    def test_search_result_with_config(self):
        """build_search_result：从 SMART_CONFIG 取搜索模型"""
        result = build_search_result(
            {"search_query": "iPhone 价格", "system_prompt": "数码助手"},
            ["前置上下文"], turns=2, tokens=300,
            conversation_id="c1",
        )
        assert result.generation_type == GenerationType.CHAT
        assert result.model == "gemini-3-pro"
        assert result.system_prompt == "数码助手"
        assert result.tool_params["_needs_google_search"] is True
        assert result.tool_params["_search_query"] == "iPhone 价格"
        assert result.search_context == "前置上下文"
        assert result.turns_used == 2
        assert result.total_tokens == 300

    @patch("config.smart_model_config.SMART_CONFIG", {})
    def test_search_result_fallback_default_model(self):
        """build_search_result：无搜索模型配置时使用默认模型"""
        result = build_search_result(
            {"search_query": "天气"}, [], turns=1, tokens=100,
        )
        assert result.generation_type == GenerationType.CHAT
        assert result.model != ""  # 应该回退到 DEFAULT_CHAT_MODEL
        assert result.tool_params["_needs_google_search"] is True

    @patch("config.smart_model_config.SMART_CONFIG", {
        "web_search": {"models": [{"id": "search-model", "priority": 1}]},
    })
    def test_search_result_no_context(self):
        """build_search_result：无上下文时 search_context 为 None"""
        result = build_search_result(
            {"search_query": "test"}, [], turns=1, tokens=100,
        )
        assert result.search_context is None

    @patch("config.smart_model_config.SMART_CONFIG", {
        "web_search": {"models": [{"id": "search-model", "priority": 1}]},
    })
    def test_search_result_empty_query(self):
        """build_search_result：无 search_query 参数时默认空字符串"""
        result = build_search_result(
            {}, [], turns=1, tokens=100,
        )
        assert result.tool_params["_search_query"] == ""


# ============================================================
# TestExtractText
# ============================================================


class TestExtractText:

    def test_single_text(self):
        loop = _make_loop()
        assert loop._extract_text([TextPart(text="hello")]) == "hello"

    def test_multiple_text(self):
        loop = _make_loop()
        content = [TextPart(text="hello"), TextPart(text="world")]
        assert loop._extract_text(content) == "hello world"

    def test_mixed_with_image(self):
        loop = _make_loop()
        content = [TextPart(text="分析"), ImagePart(url="http://img.jpg")]
        assert loop._extract_text(content) == "分析"

    def test_empty(self):
        loop = _make_loop()
        assert loop._extract_text([]) == ""
