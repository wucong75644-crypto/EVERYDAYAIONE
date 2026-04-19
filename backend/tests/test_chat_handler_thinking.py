"""ChatHandler 思考链（thinking）流式推送 + 持久化测试"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart
from services.adapters.base import CostEstimate, StreamChunk
from services.handlers.chat_handler import ChatHandler


# -- Helpers --

def _make_handler() -> ChatHandler:
    return ChatHandler(db=MagicMock())


# -- TestThinkingStream --

class TestThinkingStream:

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_thinking_chunks_pushed_via_ws(self, mock_ws, mock_factory):
        """thinking_content → build_thinking_chunk → send_to_task_or_user"""
        handler = _make_handler()
        handler._build_llm_messages = AsyncMock(return_value=[
            {"role": "user", "content": "hi"},
        ])
        handler.on_complete = AsyncMock()
        handler._dispatch_post_tasks = MagicMock()

        async def mock_stream(**kwargs):
            yield StreamChunk(thinking_content="let me think")
            yield StreamChunk(thinking_content=" about this")
            yield StreamChunk(content="answer", prompt_tokens=10, completion_tokens=5)

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream
        mock_adapter.close = AsyncMock()
        mock_adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test", estimated_cost_usd=Decimal("0"), estimated_credits=1,
        )
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_or_user = AsyncMock()

        await handler._stream_generate(
            task_id="t1", message_id="m1", conversation_id="c1",
            user_id="u1", content=[TextPart(text="hi")], model_id="test",
        )

        # 找出所有 THINKING_CHUNK 类型的 WS 消息
        from schemas.websocket import WSMessageType
        thinking_msgs = [
            call.args[2]
            for call in mock_ws.send_to_task_or_user.call_args_list
            if call.args[2].get("type") == WSMessageType.THINKING_CHUNK.value
        ]
        assert len(thinking_msgs) == 2
        # 第二条消息的 accumulated 应包含完整思考内容
        assert thinking_msgs[1]["payload"]["accumulated"] == "let me think about this"

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_thinking_content_passed_to_on_complete(self, mock_ws, mock_factory):
        """accumulated_thinking → on_complete(thinking_content=...)"""
        handler = _make_handler()
        handler._build_llm_messages = AsyncMock(return_value=[
            {"role": "user", "content": "hi"},
        ])
        handler.on_complete = AsyncMock()
        handler._dispatch_post_tasks = MagicMock()

        async def mock_stream(**kwargs):
            yield StreamChunk(thinking_content="reasoning step 1")
            yield StreamChunk(content="final answer", prompt_tokens=5, completion_tokens=2)

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream
        mock_adapter.close = AsyncMock()
        mock_adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test", estimated_cost_usd=Decimal("0"), estimated_credits=0,
        )
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_or_user = AsyncMock()

        await handler._stream_generate(
            task_id="t1", message_id="m1", conversation_id="c1",
            user_id="u1", content=[TextPart(text="hi")], model_id="test",
        )

        handler.on_complete.assert_called_once()
        call_kwargs = handler.on_complete.call_args[1]
        assert call_kwargs["thinking_content"] == "reasoning step 1"

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_no_thinking_passes_none_to_on_complete(self, mock_ws, mock_factory):
        """无 thinking chunk → on_complete(thinking_content=None)"""
        handler = _make_handler()
        handler._build_llm_messages = AsyncMock(return_value=[
            {"role": "user", "content": "hi"},
        ])
        handler.on_complete = AsyncMock()
        handler._dispatch_post_tasks = MagicMock()

        async def mock_stream(**kwargs):
            yield StreamChunk(content="hello", prompt_tokens=5, completion_tokens=1)

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream
        mock_adapter.close = AsyncMock()
        mock_adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test", estimated_cost_usd=Decimal("0"), estimated_credits=0,
        )
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_or_user = AsyncMock()

        await handler._stream_generate(
            task_id="t1", message_id="m1", conversation_id="c1",
            user_id="u1", content=[TextPart(text="hi")], model_id="test",
        )

        call_kwargs = handler.on_complete.call_args[1]
        assert call_kwargs["thinking_content"] is None


# -- TestHandleCompleteThinkingContent --

class TestHandleCompleteThinkingContent:
    """_handle_complete_common 将 thinking_content 存入 generation_params"""

    @pytest.mark.asyncio
    async def test_thinking_content_stored_in_gen_params(self):
        """thinking_content='推理过程' → extra_gen_params['thinking_content']"""
        handler = _make_handler()

        task_data = {
            "external_task_id": "task_1",
            "placeholder_message_id": "msg_1",
            "conversation_id": "conv_1",
            "user_id": "user_1",
            "model_id": "test-model",
            "client_task_id": "ct1",
            "status": "running",
            "version": 1,
            "started_at": None,
        }

        handler._get_task_context = MagicMock(return_value=task_data)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._extract_extra_gen_params = MagicMock(return_value={})

        captured_params = {}
        from datetime import datetime, timezone
        from schemas.message import Message

        def fake_upsert(**kwargs):
            captured_params.update(kwargs)
            msg = Message(
                id="msg_1", conversation_id="conv_1", role="assistant",
                content=[{"type": "text", "text": "ok"}],
                created_at=datetime.now(timezone.utc),
            )
            return msg, {"id": "msg_1", "content": [], "created_at": "2026-01-01T00:00:00+00:00"}

        handler._upsert_assistant_message = fake_upsert
        handler._complete_task = MagicMock()

        with patch("services.websocket_manager.ws_manager") as mock_ws:
            mock_ws.send_to_task_or_user = AsyncMock()

            await handler._handle_complete_common(
                task_id="task_1",
                result=[TextPart(text="ok")],
                credits_consumed=0,
                thinking_content="推理过程内容",
            )

        # 核心断言：thinking_content 写入 extra_generation_params
        assert captured_params["extra_generation_params"]["thinking_content"] == "推理过程内容"

    @pytest.mark.asyncio
    async def test_no_thinking_content_preserves_gen_params(self):
        """无 thinking_content → extra_gen_params 不受影响"""
        handler = _make_handler()

        task_data = {
            "external_task_id": "task_2",
            "placeholder_message_id": "msg_2",
            "conversation_id": "conv_2",
            "user_id": "user_2",
            "model_id": "test-model",
            "client_task_id": "ct2",
            "status": "running",
            "version": 1,
            "started_at": None,
        }

        handler._get_task_context = MagicMock(return_value=task_data)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._extract_extra_gen_params = MagicMock(return_value=None)

        captured_params = {}
        from datetime import datetime, timezone
        from schemas.message import Message

        def fake_upsert(**kwargs):
            captured_params.update(kwargs)
            msg = Message(
                id="msg_2", conversation_id="conv_2", role="assistant",
                content=[{"type": "text", "text": "ok"}],
                created_at=datetime.now(timezone.utc),
            )
            return msg, {"id": "msg_2", "content": [], "created_at": "2026-01-01T00:00:00+00:00"}

        handler._upsert_assistant_message = fake_upsert
        handler._complete_task = MagicMock()

        with patch("services.websocket_manager.ws_manager") as mock_ws:
            mock_ws.send_to_task_or_user = AsyncMock()

            await handler._handle_complete_common(
                task_id="task_2",
                result=[TextPart(text="ok")],
                credits_consumed=0,
            )

        # 无 thinking_content → extra_generation_params 为 None（未修改）
        assert captured_params["extra_generation_params"] is None


class TestHandleCompleteToolDigest:
    """_handle_complete_common 将 tool_digest 存入 generation_params"""

    @pytest.mark.asyncio
    async def test_tool_digest_stored_in_gen_params(self):
        """tool_digest 传入后应写入 extra_generation_params"""
        handler = _make_handler()

        task_data = {
            "external_task_id": "task_d1",
            "placeholder_message_id": "msg_d1",
            "conversation_id": "conv_d1",
            "user_id": "user_d1",
            "model_id": "test-model",
            "client_task_id": "ctd1",
            "status": "running",
            "version": 1,
            "started_at": None,
        }

        handler._get_task_context = MagicMock(return_value=task_data)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._extract_extra_gen_params = MagicMock(return_value={})

        captured_params = {}
        from datetime import datetime, timezone
        from schemas.message import Message

        def fake_upsert(**kwargs):
            captured_params.update(kwargs)
            msg = Message(
                id="msg_d1", conversation_id="conv_d1", role="assistant",
                content=[{"type": "text", "text": "ok"}],
                created_at=datetime.now(timezone.utc),
            )
            return msg, {"id": "msg_d1", "content": [], "created_at": "2026-01-01T00:00:00+00:00"}

        handler._upsert_assistant_message = fake_upsert
        handler._complete_task = MagicMock()

        test_digest = {
            "tools": [{"name": "erp_agent", "hint": "查订单", "ok": True}],
            "staging_dir": "staging/conv_d1",
        }

        with patch("services.websocket_manager.ws_manager") as mock_ws:
            mock_ws.send_to_task_or_user = AsyncMock()

            await handler._handle_complete_common(
                task_id="task_d1",
                result=[TextPart(text="ok")],
                credits_consumed=0,
                tool_digest=test_digest,
            )

        assert captured_params["extra_generation_params"]["tool_digest"] == test_digest

    @pytest.mark.asyncio
    async def test_both_thinking_and_digest(self):
        """thinking_content + tool_digest 同时传入，两个都应存入"""
        handler = _make_handler()

        task_data = {
            "external_task_id": "task_d2",
            "placeholder_message_id": "msg_d2",
            "conversation_id": "conv_d2",
            "user_id": "user_d2",
            "model_id": "test-model",
            "client_task_id": "ctd2",
            "status": "running",
            "version": 1,
            "started_at": None,
        }

        handler._get_task_context = MagicMock(return_value=task_data)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._extract_extra_gen_params = MagicMock(return_value={})

        captured_params = {}
        from datetime import datetime, timezone
        from schemas.message import Message

        def fake_upsert(**kwargs):
            captured_params.update(kwargs)
            msg = Message(
                id="msg_d2", conversation_id="conv_d2", role="assistant",
                content=[{"type": "text", "text": "ok"}],
                created_at=datetime.now(timezone.utc),
            )
            return msg, {"id": "msg_d2", "content": [], "created_at": "2026-01-01T00:00:00+00:00"}

        handler._upsert_assistant_message = fake_upsert
        handler._complete_task = MagicMock()

        test_digest = {"tools": [{"name": "code_execute", "hint": "df.head", "ok": True}], "staging_dir": "staging/conv_d2"}

        with patch("services.websocket_manager.ws_manager") as mock_ws:
            mock_ws.send_to_task_or_user = AsyncMock()

            await handler._handle_complete_common(
                task_id="task_d2",
                result=[TextPart(text="ok")],
                credits_consumed=0,
                thinking_content="推理过程",
                tool_digest=test_digest,
            )

        gen_params = captured_params["extra_generation_params"]
        assert gen_params["thinking_content"] == "推理过程"
        assert gen_params["tool_digest"] == test_digest
