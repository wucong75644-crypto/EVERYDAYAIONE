"""Agent Loop 核心单元测试 — 护栏 / 工具分发 / 多轮循环"""

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


# -- Helpers --

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


# -- TestAgentGuardrails --


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


# -- TestProcessToolCall --


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


# -- TestAgentLoopRun（集成测试 mock _call_brain）--


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


# -- TestUserLocationInjection --


class TestUserLocationInjection:
    """Agent Loop run() user_location 参数存储 + 系统提示词注入"""

    @pytest.mark.asyncio
    async def test_user_location_stored_on_run(self):
        """run(user_location='浙江省金华市') → self._user_location 被存储"""
        loop = _make_loop()

        mock_result = AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=0, total_tokens=0,
        )
        loop._execute_loop = AsyncMock(return_value=mock_result)

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            await loop.run(
                _text_content("天气怎么样"),
                user_location="浙江省金华市",
            )

        assert loop._user_location == "浙江省金华市"

    @pytest.mark.asyncio
    async def test_user_location_none_by_default(self):
        """run() 不传 user_location → self._user_location = None"""
        loop = _make_loop()

        mock_result = AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=0, total_tokens=0,
        )
        loop._execute_loop = AsyncMock(return_value=mock_result)

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            await loop.run(_text_content("你好"))

        assert loop._user_location is None

    @pytest.mark.asyncio
    async def test_location_injected_into_system_prompt(self):
        """user_location 被注入到 _execute_loop 的系统提示词"""
        loop = _make_loop()
        loop._user_location = "广东省深圳市"
        loop._has_image = False
        loop._thinking_mode = None
        loop._settings = MagicMock(
            agent_loop_brain_context_limit=5,
            agent_loop_brain_context_max_chars=2000,
            agent_loop_brain_max_images=3,
            agent_loop_max_turns=3,
            agent_loop_max_tokens=3000,
        )

        captured_messages = {}

        async def mock_call_brain(messages):
            captured_messages["msgs"] = messages
            return _make_brain_response(
                tool_calls=[_make_tool_call("route_to_chat", {
                    "system_prompt": "test",
                    "model": "auto",
                })],
                usage={"total_tokens": 50},
            )

        loop._call_brain = mock_call_brain
        loop._build_system_prompt = AsyncMock(return_value="base system prompt")
        loop._build_user_content = MagicMock(
            return_value=[{"type": "text", "text": "天气"}],
        )
        loop._get_recent_history = AsyncMock(return_value=None)

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = loop._settings
            await loop._execute_loop(_text_content("天气怎么样"))

        # system prompt 是 messages 列表的第一个 system 消息
        system_msg = captured_messages["msgs"][0]
        assert system_msg["role"] == "system"
        assert "用户所在位置：广东省深圳市" in system_msg["content"]
