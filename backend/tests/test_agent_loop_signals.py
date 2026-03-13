"""Agent Loop 信号 + 通知 单元测试 — 路由信号 / 工具超时 / 意图学习 / 任务推送"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart
from services.agent_types import AgentResult, AgentGuardrails
from services.agent_loop import AgentLoop


# -- Helpers --

def _make_loop() -> AgentLoop:
    """创建 AgentLoop 实例（mock db）"""
    return AgentLoop(db=MagicMock(), user_id="u1", conversation_id="c1")


def _make_tool_call(name: str, arguments: dict, tc_id: str = "tc_1") -> dict:
    return {
        "id": tc_id,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _text_content(text: str):
    return [TextPart(text=text)]


# -- TestRecordLoopSignal --

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


# -- TestSlowToolTimeout --

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


# -- TestRecordAskUserContext --

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


# -- TestCheckIntentLearning --

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


# -- TestRunTaskId --

class TestRunTaskId:

    @pytest.mark.asyncio
    async def test_run_stores_task_id(self):
        """run(task_id='t1') → self._task_id == 't1'"""
        loop = _make_loop()
        loop._execute_loop = AsyncMock(return_value=AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=0, total_tokens=0,
        ))
        loop._check_intent_learning = MagicMock()
        loop._record_loop_signal = MagicMock()

        await loop.run(_text_content("hello"), task_id="t1")

        assert loop._task_id == "t1"

    @pytest.mark.asyncio
    async def test_run_without_task_id(self):
        """run() 无 task_id → self._task_id == None"""
        loop = _make_loop()
        loop._execute_loop = AsyncMock(return_value=AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=0, total_tokens=0,
        ))
        loop._check_intent_learning = MagicMock()
        loop._record_loop_signal = MagicMock()

        await loop.run(_text_content("hello"))

        assert loop._task_id is None


# -- TestNotifyProgress --

class TestNotifyProgress:

    @pytest.mark.asyncio
    async def test_with_task_id_sends_to_task_subscribers(self):
        """有 task_id → send_to_task_subscribers"""
        loop = _make_loop()
        loop._task_id = "t1"

        mock_ws = MagicMock()
        mock_ws.send_to_task_subscribers = AsyncMock()
        mock_ws.send_to_user = AsyncMock()

        with patch("services.websocket_manager.ws_manager", new=mock_ws):
            await loop._notify_progress(1, "route_to_chat", "executing")

        mock_ws.send_to_task_subscribers.assert_called_once()
        call_args = mock_ws.send_to_task_subscribers.call_args
        assert call_args[0][0] == "t1"
        mock_ws.send_to_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_task_id_sends_to_user(self):
        """无 task_id → send_to_user"""
        loop = _make_loop()
        loop._task_id = None

        mock_ws = MagicMock()
        mock_ws.send_to_task_subscribers = AsyncMock()
        mock_ws.send_to_user = AsyncMock()

        with patch("services.websocket_manager.ws_manager", new=mock_ws):
            await loop._notify_progress(1, "route_to_chat", "executing")

        mock_ws.send_to_user.assert_called_once()
        call_args = mock_ws.send_to_user.call_args
        assert call_args[0][0] == "u1"
        mock_ws.send_to_task_subscribers.assert_not_called()

    @pytest.mark.asyncio
    async def test_notification_error_silenced(self):
        """WS 通知失败 → 静默不抛异常"""
        loop = _make_loop()
        loop._task_id = "t1"

        mock_ws = MagicMock()
        mock_ws.send_to_task_subscribers = AsyncMock(
            side_effect=RuntimeError("ws down"),
        )

        with patch("services.websocket_manager.ws_manager", new=mock_ws):
            # 不应抛异常
            await loop._notify_progress(1, "route_to_chat", "executing")


# -- TestThinkingMode --

class TestThinkingMode:

    @pytest.mark.asyncio
    async def test_run_stores_thinking_mode(self):
        """run(thinking_mode='deep_think') → self._thinking_mode == 'deep_think'"""
        loop = _make_loop()
        loop._execute_loop = AsyncMock(return_value=AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=0, total_tokens=0,
        ))
        loop._check_intent_learning = MagicMock()
        loop._record_loop_signal = MagicMock()

        await loop.run(_text_content("hello"), thinking_mode="deep_think")

        assert loop._thinking_mode == "deep_think"

    @pytest.mark.asyncio
    async def test_run_without_thinking_mode_defaults_none(self):
        """run() 不传 thinking_mode → self._thinking_mode is None"""
        loop = _make_loop()
        loop._execute_loop = AsyncMock(return_value=AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=0, total_tokens=0,
        ))
        loop._check_intent_learning = MagicMock()
        loop._record_loop_signal = MagicMock()

        await loop.run(_text_content("hello"))

        assert loop._thinking_mode is None

    @pytest.mark.asyncio
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._build_system_prompt", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    async def test_deep_think_appends_hint_to_system_prompt(
        self, mock_history, mock_prompt, mock_brain,
    ):
        """_thinking_mode='deep_think' → 系统提示词追加深度思考提示"""
        mock_prompt.return_value = "base prompt"
        mock_history.return_value = None
        mock_brain.return_value = {
            "choices": [{"message": {"content": "直接回复"}}],
            "usage": {"total_tokens": 50},
        }

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_loop_max_turns=3,
                agent_loop_max_tokens=3000,
            )
            loop = _make_loop()
            loop._thinking_mode = "deep_think"
            await loop._execute_loop(_text_content("test"))

        # 验证传给 _call_brain 的 messages 中 system 提示词包含深度思考提示
        call_args = mock_brain.call_args[0][0]
        system_msg = call_args[0]["content"]
        assert "深度思考模式" in system_msg
        assert "深度思考:✓" in system_msg

    @pytest.mark.asyncio
    async def test_call_brain_includes_enable_thinking_false(self):
        """_call_brain 请求体包含 enable_thinking: False"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="dashscope",
            agent_loop_model="qwen3.5-plus",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"total_tokens": 50},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        loop._client = mock_client

        await loop._call_brain([{"role": "user", "content": "test"}])

        call_json = mock_client.post.call_args[1]["json"]
        assert call_json["enable_thinking"] is False

        await loop.close()
