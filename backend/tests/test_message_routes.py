"""消息路由入口测试 — _resolve_generation_type / generate_message"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest
from starlette.testclient import TestClient

from datetime import datetime, timezone

from schemas.message import GenerationType, Message, TextPart, MessageOperation
from api.deps import OrgContext


def _make_message(msg_id="msg_1"):
    return Message(
        id=msg_id, conversation_id="c1", role="assistant",
        content=[], created_at=datetime.now(timezone.utc),
    )


def _make_request():
    """创建满足 slowapi 校验的 Request mock"""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [],
        "query_string": b"",
    }
    return StarletteRequest(scope)


# -- TestResolveGenerationType --

class TestResolveGenerationType:
    """_resolve_generation_type 中 thinking_mode 的提取与传递"""

    @pytest.mark.asyncio
    async def test_thinking_mode_extracted_and_forwarded(self):
        """body.params['thinking_mode']='deep_think' → agent.run(thinking_mode='deep_think')"""
        from services.agent_types import AgentResult

        mock_result = AgentResult(
            generation_type=GenerationType.CHAT,
            model="gemini-3-pro",
            turns_used=1, total_tokens=100,
        )

        body = MagicMock()
        body.model = "auto"
        body.generation_type = None
        body.operation = MessageOperation.SEND
        body.content = [TextPart(text="hello")]
        body.params = {"thinking_mode": "deep_think"}

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_agent.close = AsyncMock()

        # get_settings 和 AgentLoop 在函数内 local import
        with patch("core.config.get_settings") as mock_settings, \
             patch("services.intent_router.SMART_MODEL_ID", "auto"), \
             patch("services.agent_loop.AgentLoop", return_value=mock_agent):
            mock_settings.return_value = MagicMock(agent_loop_enabled=True)

            from api.routes.message import _resolve_generation_type
            gen_type, result = await _resolve_generation_type(
                body, "u1", "c1", db=MagicMock(),
            )

        mock_agent.run.assert_awaited_once()
        call_kwargs = mock_agent.run.call_args[1]
        assert call_kwargs["thinking_mode"] == "deep_think"
        assert gen_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_no_thinking_mode_forwards_none(self):
        """body.params 无 thinking_mode → agent.run(thinking_mode=None)"""
        from services.agent_types import AgentResult

        mock_result = AgentResult(
            generation_type=GenerationType.CHAT,
            model="gemini-3-pro",
            turns_used=1, total_tokens=100,
        )

        body = MagicMock()
        body.model = "auto"
        body.generation_type = None
        body.operation = MessageOperation.SEND
        body.content = [TextPart(text="hello")]
        body.params = {}

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_agent.close = AsyncMock()

        with patch("core.config.get_settings") as mock_settings, \
             patch("services.intent_router.SMART_MODEL_ID", "auto"), \
             patch("services.agent_loop.AgentLoop", return_value=mock_agent):
            mock_settings.return_value = MagicMock(agent_loop_enabled=True)

            from api.routes.message import _resolve_generation_type
            await _resolve_generation_type(body, "u1", "c1", db=MagicMock())

        call_kwargs = mock_agent.run.call_args[1]
        assert call_kwargs["thinking_mode"] is None

    @pytest.mark.asyncio
    async def test_params_none_defaults_thinking_mode_none(self):
        """body.params=None → thinking_mode=None（不崩溃）"""
        from services.agent_types import AgentResult

        mock_result = AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=1, total_tokens=50,
        )

        body = MagicMock()
        body.model = "auto"
        body.generation_type = None
        body.operation = MessageOperation.SEND
        body.content = [TextPart(text="hi")]
        body.params = None

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_agent.close = AsyncMock()

        with patch("core.config.get_settings") as mock_settings, \
             patch("services.intent_router.SMART_MODEL_ID", "auto"), \
             patch("services.agent_loop.AgentLoop", return_value=mock_agent):
            mock_settings.return_value = MagicMock(agent_loop_enabled=True)

            from api.routes.message import _resolve_generation_type
            await _resolve_generation_type(body, "u1", "c1", db=MagicMock())

        call_kwargs = mock_agent.run.call_args[1]
        assert call_kwargs["thinking_mode"] is None


# -- TestPrefetchedSummaryInjection --

class TestPrefetchedSummaryInjection:
    """generate_message 中 _prefetched_summary 注入到 body.params"""

    def _make_body(self, params=None):
        body = MagicMock()
        body.model = "gemini-3-pro"
        body.generation_type = GenerationType.CHAT
        body.operation = MessageOperation.SEND
        body.content = [TextPart(text="hi")]
        body.params = params
        body.created_at = None
        body.client_request_id = None
        body.original_message_id = None
        body.assistant_message_id = None
        body.placeholder_created_at = None
        body.client_task_id = "ct1"
        return body

    @pytest.mark.asyncio
    async def test_summary_injected_from_conversation(self):
        """conversation['context_summary'] → body.params['_prefetched_summary']"""
        body = self._make_body(params={})
        conversation = {"id": "c1", "context_summary": "之前讨论了Python"}

        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(return_value=conversation)

        with patch("api.routes.message._resolve_generation_type", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.create_user_message", new_callable=AsyncMock) as mock_create_msg, \
             patch("api.routes.message.handle_regenerate_or_send_operation", new_callable=AsyncMock) as mock_send, \
             patch("api.routes.message.get_handler") as mock_get_handler, \
             patch("api.routes.message.start_generation_task", new_callable=AsyncMock) as mock_start:
            mock_resolve.return_value = (GenerationType.CHAT, None)
            mock_create_msg.return_value = _make_message("msg_u1")
            mock_send.return_value = ("msg_a1", _make_message("msg_a1"))
            mock_start.return_value = "ext_task_1"

            from api.routes.message import generate_message
            await generate_message(
                request=_make_request(),
                conversation_id="c1",
                body=body,
                ctx=OrgContext(user_id="u1"),
                db=MagicMock(),
                task_limit_service=None,
            )

        assert body.params["_prefetched_summary"] == "之前讨论了Python"

    @pytest.mark.asyncio
    async def test_summary_none_when_conversation_has_no_summary(self):
        """conversation 无 context_summary → _prefetched_summary=None"""
        body = self._make_body(params=None)
        conversation = {"id": "c1"}

        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(return_value=conversation)

        with patch("api.routes.message._resolve_generation_type", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.create_user_message", new_callable=AsyncMock) as mock_create_msg, \
             patch("api.routes.message.handle_regenerate_or_send_operation", new_callable=AsyncMock) as mock_send, \
             patch("api.routes.message.get_handler") as mock_get_handler, \
             patch("api.routes.message.start_generation_task", new_callable=AsyncMock) as mock_start:
            mock_resolve.return_value = (GenerationType.CHAT, None)
            mock_create_msg.return_value = _make_message("msg_u2")
            mock_send.return_value = ("msg_a2", _make_message("msg_a2"))
            mock_start.return_value = "ext_task_2"

            from api.routes.message import generate_message
            await generate_message(
                request=_make_request(),
                conversation_id="c1",
                body=body,
                ctx=OrgContext(user_id="u1"),
                db=MagicMock(),
                task_limit_service=None,
            )

        assert body.params is not None
        assert body.params["_prefetched_summary"] is None


# -- TestUserLocationInjection --


class TestUserLocationInjection:
    """generate_message 中 IP 提取 + _user_location 注入"""

    def _make_body(self, params=None):
        body = MagicMock()
        body.model = "gemini-3-pro"
        body.generation_type = GenerationType.CHAT
        body.operation = MessageOperation.SEND
        body.content = [TextPart(text="今天天气")]
        body.params = params
        body.created_at = None
        body.client_request_id = None
        body.original_message_id = None
        body.assistant_message_id = None
        body.placeholder_created_at = None
        body.client_task_id = "ct1"
        return body

    def _make_request_with_ip(self, ip: str):
        """创建带 X-Real-IP 头的 Request"""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [(b"x-real-ip", ip.encode())],
            "query_string": b"",
        }
        return StarletteRequest(scope)

    @pytest.mark.asyncio
    async def test_location_injected_when_ip_resolves(self):
        """公网 IP 解析成功 → body.params['_user_location'] = '浙江省金华市'"""
        body = self._make_body(params={})
        request = self._make_request_with_ip("115.200.1.1")
        conversation = {"id": "c1"}

        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(return_value=conversation)

        with patch("api.routes.message._resolve_generation_type", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.create_user_message", new_callable=AsyncMock) as mock_create_msg, \
             patch("api.routes.message.handle_regenerate_or_send_operation", new_callable=AsyncMock) as mock_send, \
             patch("api.routes.message.get_handler") as mock_get_handler, \
             patch("api.routes.message.start_generation_task", new_callable=AsyncMock) as mock_start, \
             patch("services.ip_location_service.get_location_by_ip", new_callable=AsyncMock, return_value="浙江省金华市"):
            mock_resolve.return_value = (GenerationType.CHAT, None)
            mock_create_msg.return_value = _make_message("msg_u1")
            mock_send.return_value = ("msg_a1", _make_message("msg_a1"))
            mock_start.return_value = "ext_task_1"

            from api.routes.message import generate_message
            await generate_message(
                request=request,
                conversation_id="c1",
                body=body,
                ctx=OrgContext(user_id="u1"),
                db=MagicMock(),
                task_limit_service=None,
            )

        assert body.params["_user_location"] == "浙江省金华市"

    @pytest.mark.asyncio
    async def test_no_location_when_ip_returns_none(self):
        """IP 解析返回 None → _user_location 不注入"""
        body = self._make_body(params={})
        request = self._make_request_with_ip("127.0.0.1")
        conversation = {"id": "c1"}

        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(return_value=conversation)

        with patch("api.routes.message._resolve_generation_type", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.create_user_message", new_callable=AsyncMock) as mock_create_msg, \
             patch("api.routes.message.handle_regenerate_or_send_operation", new_callable=AsyncMock) as mock_send, \
             patch("api.routes.message.get_handler") as mock_get_handler, \
             patch("api.routes.message.start_generation_task", new_callable=AsyncMock) as mock_start, \
             patch("services.ip_location_service.get_location_by_ip", new_callable=AsyncMock, return_value=None):
            mock_resolve.return_value = (GenerationType.CHAT, None)
            mock_create_msg.return_value = _make_message("msg_u2")
            mock_send.return_value = ("msg_a2", _make_message("msg_a2"))
            mock_start.return_value = "ext_task_2"

            from api.routes.message import generate_message
            await generate_message(
                request=request,
                conversation_id="c1",
                body=body,
                ctx=OrgContext(user_id="u1"),
                db=MagicMock(),
                task_limit_service=None,
            )

        assert "_user_location" not in body.params

    @pytest.mark.asyncio
    async def test_location_task_exception_degrades_gracefully(self):
        """IP 定位异常 → 不崩溃，_user_location 不注入"""
        body = self._make_body(params=None)
        request = self._make_request_with_ip("1.2.3.4")
        conversation = {"id": "c1"}

        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(return_value=conversation)

        with patch("api.routes.message._resolve_generation_type", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.create_user_message", new_callable=AsyncMock) as mock_create_msg, \
             patch("api.routes.message.handle_regenerate_or_send_operation", new_callable=AsyncMock) as mock_send, \
             patch("api.routes.message.get_handler") as mock_get_handler, \
             patch("api.routes.message.start_generation_task", new_callable=AsyncMock) as mock_start, \
             patch("services.ip_location_service.get_location_by_ip", new_callable=AsyncMock, side_effect=Exception("API timeout")):
            mock_resolve.return_value = (GenerationType.CHAT, None)
            mock_create_msg.return_value = _make_message("msg_u3")
            mock_send.return_value = ("msg_a3", _make_message("msg_a3"))
            mock_start.return_value = "ext_task_3"

            from api.routes.message import generate_message
            await generate_message(
                request=request,
                conversation_id="c1",
                body=body,
                ctx=OrgContext(user_id="u1"),
                db=MagicMock(),
                task_limit_service=None,
            )

        # params 应被创建但不包含 _user_location
        if body.params is not None:
            assert "_user_location" not in body.params
