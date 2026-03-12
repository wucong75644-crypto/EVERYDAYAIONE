"""
Agent Loop + AgentGuardrails 单元测试

覆盖：护栏（循环检测/token预算）、工具分发、结果构建、多轮循环、
      对话历史注入、客户端管理、路由信号记录
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_types import AgentResult, AgentGuardrails, PendingAsyncTool
from services.agent_loop import AgentLoop
from services.agent_result_builder import (
    build_chat_result,
    build_final_result,
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
        routing_holder = {}
        await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            routing_holder=routing_holder,
        )
        assert len(tool_results) == 1
        assert tool_results[0]["is_error"] is True

    @pytest.mark.asyncio
    async def test_loop_detected_sets_abort_flag(self):
        """循环检测→设置 _loop_abort 标志"""
        loop = _make_loop()
        loop._settings = MagicMock()
        guardrails = AgentGuardrails()
        args = {"search_query": "same query"}
        # 先填充2次
        guardrails.detect_loop("web_search", args)
        guardrails.detect_loop("web_search", args)

        tc = _make_tool_call("web_search", args)
        routing_holder = {}
        await loop._process_tool_call(
            tc, turn=2,
            guardrails=guardrails,
            tool_results=[],
            accumulated_context=["prev context"],
            routing_holder=routing_holder,
        )
        assert routing_holder.get("_loop_abort") is True

    @pytest.mark.asyncio
    async def test_ask_user_records_routing_decision(self):
        """ask_user→记录路由决策"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("ask_user", {
            "message": "请提供更多信息",
            "reason": "need_info",
        })
        tool_results = []
        routing_holder = {}
        await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            routing_holder=routing_holder,
        )
        assert routing_holder["decision"]["tool_name"] == "ask_user"
        assert routing_holder["decision"]["arguments"]["message"] == "请提供更多信息"
        assert len(tool_results) == 1
        assert "询问" in tool_results[0]["content"]

    @pytest.mark.asyncio
    async def test_route_to_chat_records_routing_decision(self):
        """route_to_chat→记录路由决策"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("route_to_chat", {
            "system_prompt": "你是翻译专家",
            "model": "gemini-3-pro",
        })
        tool_results = []
        routing_holder = {}
        await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            routing_holder=routing_holder,
        )
        assert routing_holder["decision"]["tool_name"] == "route_to_chat"
        assert routing_holder["decision"]["arguments"]["model"] == "gemini-3-pro"
        assert "gemini-3-pro" in tool_results[0]["content"]

    @pytest.mark.asyncio
    async def test_route_to_image_records_routing_decision(self):
        """route_to_image→记录路由决策"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("route_to_image", {
            "prompts": [{"prompt": "a cat", "aspect_ratio": "1:1"}],
            "model": "flux-kontext",
        })
        tool_results = []
        routing_holder = {}
        await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            routing_holder=routing_holder,
        )
        assert routing_holder["decision"]["tool_name"] == "route_to_image"
        assert "1 张图片" in tool_results[0]["content"]

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock)
    async def test_sync_tool_executor_error_returns_error_result(self, mock_notify):
        """同步工具 executor 异常→error result"""
        loop = _make_loop()
        loop._settings = MagicMock()
        loop.executor.execute = AsyncMock(side_effect=Exception("timeout"))
        tc = _make_tool_call("get_conversation_context", {"limit": 10})
        tool_results = []
        routing_holder = {}
        await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            routing_holder=routing_holder,
        )
        assert tool_results[0]["is_error"] is True
        assert "timeout" in tool_results[0]["content"]

    @pytest.mark.asyncio
    async def test_malformed_arguments_parsed_as_empty(self):
        """畸形 arguments JSON→解析为 {} → 验证失败"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = {
            "id": "tc_1",
            "function": {"name": "route_to_chat", "arguments": "not json!!!"},
        }
        tool_results = []
        routing_holder = {}
        await loop._process_tool_call(
            tc, turn=0,
            guardrails=AgentGuardrails(),
            tool_results=tool_results,
            accumulated_context=[],
            routing_holder=routing_holder,
        )
        # validate_tool_call 会因缺少 required 字段返回 False
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
    async def test_routing_tool_returns_final_result(self, mock_brain, mock_prompt):
        """路由工具→build_final_result"""
        mock_prompt.return_value = "system"
        mock_brain.return_value = _make_brain_response(
            tool_calls=[_make_tool_call("route_to_chat", {
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
    async def test_route_to_image_single(self, mock_brain, mock_prompt):
        """route_to_image 单图→IMAGE result"""
        mock_prompt.return_value = "system"
        mock_brain.return_value = _make_brain_response(
            tool_calls=[_make_tool_call("route_to_image", {
                "prompts": [{"prompt": "a sunset", "aspect_ratio": "16:9"}],
                "model": "flux-kontext",
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
        assert result.tool_params["aspect_ratio"] == "16:9"
        assert result.render_hints is not None

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_route_to_image_batch(self, mock_brain, mock_prompt):
        """route_to_image 批量→batch_prompts"""
        mock_prompt.return_value = "system"
        prompts = [
            {"prompt": "cat", "aspect_ratio": "1:1"},
            {"prompt": "dog", "aspect_ratio": "1:1"},
        ]
        mock_brain.return_value = _make_brain_response(
            tool_calls=[_make_tool_call("route_to_image", {
                "prompts": prompts,
                "model": "flux-kontext",
            })],
        )

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            loop = _make_loop()
            result = await loop.run(_text_content("画两张图"))

        assert result.generation_type == GenerationType.IMAGE
        assert result.batch_prompts == prompts

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
                    {"limit": call_count * 10},
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

            with patch(
                "services.agent_loop.AgentLoop._notify_progress",
                new_callable=AsyncMock,
            ):
                result = await loop.run(_text_content("test"))

        # 第二轮检查 should_abort → True → graceful timeout
        assert result.total_tokens == 5000

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_multi_turn_info_then_route(self, mock_brain, mock_prompt, _):
        """多轮串联：turn1 get_conversation_context → turn2 route_to_chat"""
        mock_prompt.return_value = "system"
        responses = [
            _make_brain_response(
                tool_calls=[_make_tool_call(
                    "get_conversation_context", {"limit": 10},
                )],
            ),
            _make_brain_response(
                tool_calls=[_make_tool_call("route_to_chat", {
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
    """测试 agent_result_builder 的构建函数"""

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

    def test_final_result_route_to_chat(self):
        """route_to_chat → CHAT result"""
        holder = {
            "decision": {
                "tool_name": "route_to_chat",
                "arguments": {
                    "system_prompt": "翻译",
                    "model": "gpt-4",
                    "needs_google_search": True,
                },
            },
        }
        result = build_final_result(holder, ["ctx"], turns=1, tokens=100)
        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt == "翻译"
        assert result.model == "gpt-4"
        assert result.search_context == "ctx"
        assert result.tool_params["_needs_google_search"] is True

    def test_final_result_route_to_image_single(self):
        """route_to_image 单图 → IMAGE result"""
        holder = {
            "decision": {
                "tool_name": "route_to_image",
                "arguments": {
                    "prompts": [{"prompt": "cat", "aspect_ratio": "1:1"}],
                    "model": "flux",
                },
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.IMAGE
        assert result.tool_params["prompt"] == "cat"
        assert result.tool_params["aspect_ratio"] == "1:1"
        assert result.render_hints is not None

    def test_final_result_route_to_image_batch(self):
        """route_to_image 批量 → batch_prompts"""
        prompts = [{"prompt": "a"}, {"prompt": "b"}]
        holder = {
            "decision": {
                "tool_name": "route_to_image",
                "arguments": {"prompts": prompts, "model": "flux"},
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.IMAGE
        assert result.batch_prompts == prompts

    def test_final_result_route_to_video(self):
        """route_to_video → VIDEO result"""
        holder = {
            "decision": {
                "tool_name": "route_to_video",
                "arguments": {"prompt": "waves", "model": "vidu"},
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.VIDEO
        assert result.render_hints is not None

    def test_final_result_ask_user(self):
        """ask_user → direct_reply"""
        holder = {
            "decision": {
                "tool_name": "ask_user",
                "arguments": {"message": "需要更多信息", "reason": "need_info"},
            },
        }
        result = build_final_result(holder, ["ctx"], turns=1, tokens=100)
        assert result.direct_reply == "需要更多信息"
        assert result.tool_params["_ask_reason"] == "need_info"
        assert result.search_context == "ctx"

    def test_final_result_no_decision_fallback(self):
        """无路由决策→fallback chat"""
        result = build_final_result({}, ["ctx"], turns=1, tokens=100)
        assert result.generation_type == GenerationType.CHAT

    def test_final_result_unknown_tool_fallback(self):
        """未知路由工具名→fallback chat"""
        holder = {
            "decision": {
                "tool_name": "unknown_tool",
                "arguments": {"foo": "bar"},
            },
        }
        result = build_final_result(holder, ["ctx"], turns=1, tokens=100)
        assert result.generation_type == GenerationType.CHAT

    def test_final_result_image_missing_aspect_ratio(self):
        """route_to_image 单图无 aspect_ratio→默认 1:1"""
        holder = {
            "decision": {
                "tool_name": "route_to_image",
                "arguments": {
                    "prompts": [{"prompt": "a cat"}],
                    "model": "flux",
                },
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.tool_params["aspect_ratio"] == "1:1"

    def test_final_result_chat_no_google_search(self):
        """route_to_chat 未指定 needs_google_search→默认 False"""
        holder = {
            "decision": {
                "tool_name": "route_to_chat",
                "arguments": {
                    "system_prompt": "助手",
                    "model": "gemini-3-pro",
                },
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.tool_params["_needs_google_search"] is False

    def test_graceful_timeout_with_context(self):
        """graceful timeout + context→chat with context"""
        result = build_graceful_timeout(["搜索结果"], turns=3, tokens=3000)
        assert result.generation_type == GenerationType.CHAT
        assert result.search_context == "搜索结果"

    def test_graceful_timeout_empty(self):
        """graceful timeout + 全空→DEFAULT_CHAT_MODEL"""
        result = build_graceful_timeout([], turns=3, tokens=3000)
        assert result.generation_type == GenerationType.CHAT
        assert result.model != ""  # 应该有默认模型


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


# ============================================================
# TestBuildRoutingConfirmation
# ============================================================


class TestBuildRoutingConfirmation:

    def test_route_to_chat(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation(
            "route_to_chat", {"model": "gpt-4"},
        )
        assert "gpt-4" in msg

    def test_route_to_image(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation(
            "route_to_image", {"prompts": [{"prompt": "a"}, {"prompt": "b"}]},
        )
        assert "2 张图片" in msg

    def test_route_to_video(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation("route_to_video", {})
        assert "视频" in msg

    def test_ask_user(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation("ask_user", {})
        assert "询问" in msg

    def test_unknown_tool(self):
        """未知路由工具→返回默认确认"""
        loop = _make_loop()
        msg = loop._build_routing_confirmation("unknown_tool", {})
        assert msg == "已确认"


# ============================================================
# TestGetRecentHistory — 对话历史注入
# ============================================================


class TestGetRecentHistory:

    def _make_settings(
        self, limit: int = 10, max_chars: int = 3000, max_images: int = 8,
    ):
        s = MagicMock()
        s.agent_loop_brain_context_limit = limit
        s.agent_loop_brain_context_max_chars = max_chars
        s.agent_loop_brain_max_images = max_images
        return s

    @pytest.mark.asyncio
    async def test_normal_messages(self):
        """正常消息→返回结构化多模态消息列表"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "你好"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "你好！"}],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 2
        # 验证角色和内容结构
        roles = [m["role"] for m in result]
        assert "user" in roles
        assert "assistant" in roles
        # 验证内容是 content blocks 格式
        user_msg = [m for m in result if m["role"] == "user"][0]
        assert user_msg["content"][0]["type"] == "text"
        assert "你好" in user_msg["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_messages_with_images(self):
        """含图片消息→包含 image_url content block"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "帮我处理"},
                        {"type": "image", "url": "https://img.com/a.jpg"},
                    ],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        assert len(result) == 1
        blocks = result[0]["content"]
        # 验证文本 block
        text_blocks = [b for b in blocks if b["type"] == "text"]
        assert any("帮我处理" in b["text"] for b in text_blocks)
        # 验证图片 block（DB image → OpenAI image_url）
        img_blocks = [b for b in blocks if b["type"] == "image_url"]
        assert len(img_blocks) == 1
        assert img_blocks[0]["image_url"]["url"] == "https://img.com/a.jpg"

    @pytest.mark.asyncio
    async def test_empty_messages_returns_none(self):
        """空消息列表→None"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {"messages": []}

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is None

    @pytest.mark.asyncio
    async def test_char_limit_truncation(self):
        """超过 max_chars→截断"""
        loop = _make_loop()
        loop._settings = self._make_settings(max_chars=30)

        long_text = "A" * 50
        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": long_text}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "reply"}],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        # max_chars=30，第一条消息(50字)超限 → 只拿到 reply
        # reversed() 遍历：从旧到新，reply(5字) 先进入，然后 50字超限 break
        if result is not None:
            assert len(result) <= 1  # 不会返回完整 2 条

    @pytest.mark.asyncio
    async def test_service_error_returns_none(self):
        """MessageService 抛异常→None（不影响主流程）"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        with patch(
            "services.message_service.MessageService",
            side_effect=Exception("db error"),
        ):
            result = await loop._get_recent_history()

        assert result is None

    @pytest.mark.asyncio
    async def test_image_without_url_skipped(self):
        """image 类型但无 url→不生成 image_url block"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "test"},
                        {"type": "image", "url": ""},
                    ],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        blocks = result[0]["content"]
        img_blocks = [b for b in blocks if b["type"] == "image_url"]
        assert len(img_blocks) == 0


# ============================================================
# TestGetClient — HTTP 客户端管理
# ============================================================


class TestGetClient:

    @pytest.mark.asyncio
    async def test_creates_new_client(self):
        """首次调用→创建新客户端"""
        loop = _make_loop()
        loop._settings = MagicMock(
            dashscope_base_url="https://api.example.com",
            dashscope_api_key="sk-test",
            agent_loop_timeout=10.0,
        )

        client = await loop._get_client()
        assert client is not None
        assert loop._client is client
        await loop.close()

    @pytest.mark.asyncio
    async def test_reuses_existing_client(self):
        """已有未关闭客户端→复用"""
        loop = _make_loop()
        loop._settings = MagicMock(
            dashscope_base_url="https://api.example.com",
            dashscope_api_key="sk-test",
            agent_loop_timeout=10.0,
        )

        client1 = await loop._get_client()
        client2 = await loop._get_client()
        assert client1 is client2
        await loop.close()

    @pytest.mark.asyncio
    async def test_openrouter_provider_uses_openrouter_config(self):
        """provider=openrouter → 使用 OpenRouter 配置"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="openrouter",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_api_key="or-test-key",
            openrouter_app_title="TestApp",
            agent_loop_timeout=10.0,
        )

        client = await loop._get_client()
        assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
        assert client.headers["authorization"] == "Bearer or-test-key"
        assert client.headers["x-title"] == "TestApp"
        assert client.headers["http-referer"] == "https://everydayai.one"
        await loop.close()

    @pytest.mark.asyncio
    async def test_dashscope_provider_no_extra_headers(self):
        """provider=dashscope → 无 X-Title/HTTP-Referer 头"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="dashscope",
            dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            dashscope_api_key="sk-dash",
            agent_loop_timeout=10.0,
        )

        client = await loop._get_client()
        assert str(client.base_url).rstrip("/") == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert client.headers["authorization"] == "Bearer sk-dash"
        assert "x-title" not in client.headers
        assert "http-referer" not in client.headers
        await loop.close()


# ============================================================
# TestClose — 资源释放
# ============================================================


class TestClose:

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        """无客户端→安全 no-op"""
        loop = _make_loop()
        await loop.close()  # 不抛异常
        assert loop._client is None

    @pytest.mark.asyncio
    async def test_close_releases_client(self):
        """关闭后 _client 置 None"""
        loop = _make_loop()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        loop._client = mock_client

        await loop.close()
        mock_client.aclose.assert_awaited_once()
        assert loop._client is None

    @pytest.mark.asyncio
    async def test_close_skips_already_closed(self):
        """已关闭的客户端→跳过"""
        loop = _make_loop()
        mock_client = AsyncMock()
        mock_client.is_closed = True
        loop._client = mock_client

        await loop.close()
        mock_client.aclose.assert_not_awaited()


# ============================================================
# TestCallBrainModelSelection — provider 模型选择
# ============================================================


class TestCallBrainModelSelection:

    @pytest.mark.asyncio
    async def test_openrouter_uses_openrouter_model(self):
        """provider=openrouter → 使用 agent_loop_openrouter_model"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="openrouter",
            agent_loop_openrouter_model="anthropic/claude-sonnet-4.6",
            agent_loop_model="qwen3.5-plus",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = _make_brain_response(content="hi")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        loop._client = mock_client

        result = await loop._call_brain([{"role": "user", "content": "test"}])

        call_json = mock_client.post.call_args[1]["json"]
        assert call_json["model"] == "anthropic/claude-sonnet-4.6"

        await loop.close()

    @pytest.mark.asyncio
    async def test_dashscope_uses_dashscope_model(self):
        """provider=dashscope → 使用 agent_loop_model"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="dashscope",
            agent_loop_openrouter_model="anthropic/claude-sonnet-4.6",
            agent_loop_model="qwen3.5-plus",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = _make_brain_response(content="hi")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        loop._client = mock_client

        result = await loop._call_brain([{"role": "user", "content": "test"}])

        call_json = mock_client.post.call_args[1]["json"]
        assert call_json["model"] == "qwen3.5-plus"

        await loop.close()


# ============================================================
# TestRecordLoopSignal — 路由信号记录
# ============================================================


class TestRecordLoopSignal:

    @pytest.mark.asyncio
    async def test_creates_async_task(self):
        """_record_loop_signal 创建异步任务"""
        loop = _make_loop()
        result = AgentResult(
            generation_type=GenerationType.CHAT,
            model="gemini-3-pro",
            turns_used=1,
            total_tokens=100,
        )

        with patch("services.agent_loop.asyncio.create_task") as mock_task:
            loop._record_loop_signal(result, input_length=10, has_image=False)
            mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_with_file_flag(self):
        """has_file=True 参数传递"""
        loop = _make_loop()
        result = AgentResult(
            generation_type=GenerationType.IMAGE,
            model="flux",
            turns_used=2,
            total_tokens=500,
        )

        with patch("services.agent_loop.asyncio.create_task") as mock_task:
            loop._record_loop_signal(
                result, input_length=20, has_image=True, has_file=True,
            )
            mock_task.assert_called_once()


# ============================================================
# TestSlowToolTimeout — 慢速工具超时配置
# ============================================================


class TestSlowToolTimeout:

    def test_social_crawler_has_180s_timeout(self):
        from services.agent_loop import _SLOW_TOOL_TIMEOUT
        assert _SLOW_TOOL_TIMEOUT["social_crawler"] == 180.0

    def test_default_timeout_is_30s(self):
        from services.agent_loop import _SLOW_TOOL_TIMEOUT
        assert _SLOW_TOOL_TIMEOUT.get("web_search", 30.0) == 30.0
        assert _SLOW_TOOL_TIMEOUT.get("nonexistent_tool", 30.0) == 30.0

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock)
    async def test_info_tool_uses_wait_for(self, mock_notify):
        """INFO 工具执行使用 asyncio.wait_for 超时保护"""
        import asyncio
        loop = _make_loop()
        loop._settings = MagicMock()
        loop.executor.execute = AsyncMock(return_value="result")

        tc = _make_tool_call("web_search", {"search_query": "test"})
        tool_results = []

        with patch("services.agent_loop.asyncio.wait_for", new_callable=AsyncMock) as mock_wf:
            mock_wf.return_value = "result"
            await loop._process_tool_call(
                tc, turn=0,
                guardrails=AgentGuardrails(),
                tool_results=tool_results,
                accumulated_context=[],
                routing_holder={},
            )
            mock_wf.assert_awaited_once()
            # 验证 timeout 参数为默认 30.0
            call_kwargs = mock_wf.call_args
            assert call_kwargs.kwargs.get("timeout") == 30.0

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._notify_progress", new_callable=AsyncMock)
    async def test_timeout_error_returns_error_result(self, mock_notify):
        """asyncio.TimeoutError→返回超时错误"""
        import asyncio
        loop = _make_loop()
        loop._settings = MagicMock()

        tc = _make_tool_call("web_search", {"search_query": "test"})
        tool_results = []

        with patch(
            "services.agent_loop.asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError(),
        ):
            await loop._process_tool_call(
                tc, turn=0,
                guardrails=AgentGuardrails(),
                tool_results=tool_results,
                accumulated_context=[],
                routing_holder={},
            )
        assert len(tool_results) == 1
        assert tool_results[0]["is_error"] is True
        assert "超时" in tool_results[0]["content"]


# ============================================================
# TestBuildUserContent — 多模态用户消息构建
# ============================================================


class TestBuildUserContent:

    def test_text_only(self):
        """纯文本→只有 text block"""
        loop = _make_loop()
        content = [TextPart(text="你好")]
        result = loop._build_user_content(content)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "你好"

    def test_text_with_images(self):
        """文本+图片→text block + image_url blocks"""
        loop = _make_loop()
        content = [
            TextPart(text="分析图片"),
            ImagePart(url="https://img.com/a.jpg"),
            ImagePart(url="https://img.com/b.jpg"),
        ]
        result = loop._build_user_content(content)
        assert len(result) == 3
        assert result[0]["type"] == "text"
        text_blocks = [b for b in result if b["type"] == "text"]
        img_blocks = [b for b in result if b["type"] == "image_url"]
        assert len(text_blocks) == 1
        assert len(img_blocks) == 2
        assert img_blocks[0]["image_url"]["url"] == "https://img.com/a.jpg"

    def test_images_only(self):
        """只有图片（无文本）→只有 image_url blocks"""
        loop = _make_loop()
        content = [ImagePart(url="https://img.com/a.jpg")]
        result = loop._build_user_content(content)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    def test_image_without_url_skipped(self):
        """ImagePart.url=None→不生成 image_url block"""
        loop = _make_loop()
        content = [TextPart(text="test"), ImagePart(url=None)]
        result = loop._build_user_content(content)
        img_blocks = [b for b in result if b["type"] == "image_url"]
        assert len(img_blocks) == 0

    def test_file_adds_pdf_hint(self):
        """FilePart→文本前缀添加 PDF 提示"""
        from schemas.message import FilePart
        loop = _make_loop()
        content = [
            TextPart(text="解读文档"),
            FilePart(url="https://f.com/a.pdf", name="a.pdf", mime_type="application/pdf"),
        ]
        result = loop._build_user_content(content)
        text_block = result[0]
        assert "PDF文档" in text_block["text"]
        assert "解读文档" in text_block["text"]

    def test_multiple_files_count(self):
        """多个 FilePart→正确计数"""
        from schemas.message import FilePart
        loop = _make_loop()
        content = [
            TextPart(text="对比"),
            FilePart(url="u1", name="a.pdf", mime_type="application/pdf"),
            FilePart(url="u2", name="b.pdf", mime_type="application/pdf"),
        ]
        result = loop._build_user_content(content)
        assert "2份PDF" in result[0]["text"]

    def test_empty_content_returns_empty_text_block(self):
        """空内容→返回空 text block"""
        loop = _make_loop()
        result = loop._build_user_content([])
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == ""

    def test_text_with_file_and_image(self):
        """文本+文件+图片→PDF提示 + text + image_url"""
        from schemas.message import FilePart
        loop = _make_loop()
        content = [
            TextPart(text="分析"),
            FilePart(url="u1", name="a.pdf", mime_type="application/pdf"),
            ImagePart(url="https://img.com/x.jpg"),
        ]
        result = loop._build_user_content(content)
        text_blocks = [b for b in result if b["type"] == "text"]
        img_blocks = [b for b in result if b["type"] == "image_url"]
        assert len(text_blocks) == 1
        assert "PDF" in text_blocks[0]["text"]
        assert len(img_blocks) == 1


# ============================================================
# TestBuildSystemPrompt — 系统提示词构建
# ============================================================


class TestBuildSystemPrompt:

    @pytest.mark.asyncio
    async def test_empty_text_returns_base_prompt(self):
        """空文本→返回基础提示词，不查知识库"""
        loop = _make_loop()
        loop._settings = MagicMock()
        result = await loop._build_system_prompt([])
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        assert result == AGENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    @patch("services.knowledge_service.search_relevant", new_callable=AsyncMock)
    async def test_knowledge_injected(self, mock_search):
        """有知识→注入经验知识"""
        mock_search.return_value = [
            {"title": "经验1", "content": "内容1"},
            {"title": "经验2", "content": "内容2"},
        ]
        loop = _make_loop()
        loop._settings = MagicMock()
        result = await loop._build_system_prompt([TextPart(text="画猫")])
        assert "经验知识" in result
        assert "经验1" in result
        assert "内容2" in result

    @pytest.mark.asyncio
    @patch("services.knowledge_service.search_relevant", new_callable=AsyncMock)
    async def test_no_knowledge_returns_base(self, mock_search):
        """知识库无结果→返回基础提示词"""
        mock_search.return_value = []
        loop = _make_loop()
        loop._settings = MagicMock()
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        result = await loop._build_system_prompt([TextPart(text="你好")])
        assert result == AGENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    @patch("services.knowledge_service.search_relevant", new_callable=AsyncMock)
    async def test_knowledge_error_returns_base(self, mock_search):
        """知识服务异常→返回基础提示词（不影响主流程）"""
        mock_search.side_effect = Exception("db timeout")
        loop = _make_loop()
        loop._settings = MagicMock()
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        result = await loop._build_system_prompt([TextPart(text="test")])
        assert result == AGENT_SYSTEM_PROMPT


# ============================================================
# TestRecordAskUserContext — 意图学习 ask_user 上下文记录
# ============================================================


class TestRecordAskUserContext:

    @pytest.mark.asyncio
    @patch("services.intent_learning.record_ask_user_context", new_callable=AsyncMock)
    async def test_records_when_user_text_exists(self, mock_record):
        """有 user_text 时→创建记录任务"""
        loop = _make_loop()
        loop._user_text = "修正图片"
        loop._record_ask_user_context("1.编辑 2.生成")
        await asyncio.sleep(0.05)
        mock_record.assert_called_once()
        kw = mock_record.call_args[1]
        assert kw["original_message"] == "修正图片"
        assert kw["ask_options"] == "1.编辑 2.生成"

    def test_skips_when_no_user_text(self):
        """无 user_text 时→不创建任务"""
        loop = _make_loop()
        loop._user_text = ""
        with patch("asyncio.create_task") as mock_task:
            loop._record_ask_user_context("options")
            mock_task.assert_not_called()

    def test_skips_when_user_text_not_set(self):
        """未设置 _user_text 属性→不创建任务"""
        loop = _make_loop()
        with patch("asyncio.create_task") as mock_task:
            loop._record_ask_user_context("options")
            mock_task.assert_not_called()


# ============================================================
# TestCheckIntentLearning — 意图学习路由确认检查
# ============================================================


class TestCheckIntentLearning:

    def _make_result(self, gen_type="image", model="qwen-vl-max", tool_params=None):
        return AgentResult(
            generation_type=GenerationType(gen_type),
            model=model or "",
            tool_params=tool_params or {},
        )

    def test_skips_ask_user_result(self):
        """ask_user 结果→不检查"""
        loop = _make_loop()
        result = self._make_result(
            tool_params={"_ask_reason": "ambiguous"},
        )
        with patch("asyncio.create_task") as mock_task:
            loop._check_intent_learning(result, "修正图片")
            mock_task.assert_not_called()

    def test_skips_no_model(self):
        """无模型（纯兜底）→不检查"""
        loop = _make_loop()
        result = self._make_result(model="")
        with patch("asyncio.create_task") as mock_task:
            loop._check_intent_learning(result, "修正图片")
            mock_task.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.intent_learning.check_and_record_intent", new_callable=AsyncMock)
    async def test_creates_task_on_valid_route(self, mock_check):
        """有效路由→创建检查任务"""
        loop = _make_loop()
        result = self._make_result(gen_type="image", model="qwen-vl-max")
        loop._check_intent_learning(result, "修正图片")
        await asyncio.sleep(0.05)
        mock_check.assert_called_once()
        kw = mock_check.call_args[1]
        assert kw["confirmed_tool"] == "route_to_image"
        assert kw["user_response"] == "修正图片"
