"""
ChatHandler 流式生成 + 积分计算测试

覆盖：积分计算逻辑、流式生成主路径、direct_reply 路径、积分扣除
"""

import math
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart
from services.adapters.base import CostEstimate, StreamChunk
from services.handlers.chat_handler import ChatHandler


# ============================================================
# Helpers
# ============================================================


def _make_handler() -> ChatHandler:
    """创建 ChatHandler（mock db）"""
    return ChatHandler(db=MagicMock())


# ============================================================
# TestCalculateCredits
# ============================================================


class TestCalculateCredits:

    def test_api_credits_ceil_plus_1(self):
        """api_credits=5.3 → ceil(5.3)+1=7"""
        handler = _make_handler()
        handler._adapter = MagicMock()
        result = handler._calculate_credits({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "api_credits": 5.3,
        })
        assert result == math.ceil(5.3) + 1  # 7

    def test_api_credits_zero(self):
        """api_credits=0 → ceil(0)+1=1"""
        handler = _make_handler()
        handler._adapter = MagicMock()
        result = handler._calculate_credits({
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "api_credits": 0,
        })
        # ceil(0)+1 = 1, then max(1,1) = 1
        assert result == 1

    def test_api_credits_none_uses_adapter(self):
        """api_credits=None → adapter 本地估算"""
        handler = _make_handler()
        handler._adapter = MagicMock()
        handler._adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test",
            estimated_cost_usd=Decimal("0.01"),
            estimated_credits=5,
        )
        result = handler._calculate_credits({
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        })
        assert result == 5
        handler._adapter.estimate_cost_unified.assert_called_once_with(
            input_tokens=1000, output_tokens=500,
        )

    def test_credits_minimum_1_when_positive(self):
        """credits>0 时最小为 1"""
        handler = _make_handler()
        handler._adapter = MagicMock()
        # api_credits=0.1 → ceil=1 + 1 = 2 → max(1,2) = 2
        result = handler._calculate_credits({
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "api_credits": 0.1,
        })
        assert result >= 1

    def test_credits_zero_for_free_model(self):
        """credits=0 不强制（免费模型场景）"""
        handler = _make_handler()
        handler._adapter = MagicMock()
        handler._adapter.estimate_cost_unified.return_value = CostEstimate(
            model="free-model",
            estimated_cost_usd=Decimal("0"),
            estimated_credits=0,
        )
        result = handler._calculate_credits({
            "prompt_tokens": 0,
            "completion_tokens": 0,
        })
        assert result == 0


# ============================================================
# TestStreamGenerate — 流式主路径
# ============================================================


class TestStreamGenerate:

    @pytest.mark.asyncio
    async def test_direct_reply_skips_llm(self):
        """_direct_reply → 跳过 LLM 走 _stream_direct_reply"""
        handler = _make_handler()
        handler._stream_direct_reply = AsyncMock()

        await handler._stream_generate(
            task_id="t1",
            message_id="m1",
            conversation_id="c1",
            user_id="u1",
            content=[TextPart(text="test")],
            model_id="model",
            _params={"_direct_reply": "大脑直接回复"},
        )

        handler._stream_direct_reply.assert_awaited_once_with(
            task_id="t1",
            message_id="m1",
            conversation_id="c1",
            text="大脑直接回复",
        )

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_normal_stream_3_chunks(self, mock_ws, mock_factory):
        """正常流式 3 chunk 累加文字"""
        handler = _make_handler()
        handler._build_llm_messages = AsyncMock(return_value=[
            {"role": "user", "content": "hi"},
        ])
        handler.on_complete = AsyncMock()
        handler._dispatch_post_tasks = MagicMock()

        # Mock adapter
        async def mock_stream_chat(**kwargs):
            yield StreamChunk(content="你", prompt_tokens=0, completion_tokens=0)
            yield StreamChunk(content="好", prompt_tokens=0, completion_tokens=0)
            yield StreamChunk(
                content="！", finish_reason="stop",
                prompt_tokens=10, completion_tokens=3,
            )

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream_chat
        mock_adapter.close = AsyncMock()
        mock_adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test", estimated_cost_usd=Decimal("0"), estimated_credits=2,
        )
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_subscribers = AsyncMock()

        await handler._stream_generate(
            task_id="t1",
            message_id="m1",
            conversation_id="c1",
            user_id="u1",
            content=[TextPart(text="hi")],
            model_id="test-model",
        )

        # 验证 on_complete 被调用
        handler.on_complete.assert_awaited_once()
        call_args = handler.on_complete.call_args
        result_parts = call_args.kwargs["result"]
        assert result_parts[0].text == "你好！"

        # 验证 adapter 被关闭
        mock_adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_adapter_exception_calls_handle_failure(self, mock_ws, mock_factory):
        """adapter 异常→_handle_stream_failure"""
        handler = _make_handler()
        handler._build_llm_messages = AsyncMock(return_value=[])
        handler._handle_stream_failure = AsyncMock()

        async def failing_stream(**kwargs):
            raise RuntimeError("connection reset")
            yield  # noqa: unreachable

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = failing_stream
        mock_adapter.close = AsyncMock()
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_subscribers = AsyncMock()

        await handler._stream_generate(
            task_id="t1",
            message_id="m1",
            conversation_id="c1",
            user_id="u1",
            content=[TextPart(text="hi")],
            model_id="test-model",
        )

        handler._handle_stream_failure.assert_awaited_once()
        mock_adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_finally_always_closes_adapter(self, mock_ws, mock_factory):
        """finally 始终关闭 adapter"""
        handler = _make_handler()
        handler._build_llm_messages = AsyncMock(return_value=[])
        handler._handle_stream_failure = AsyncMock()

        async def empty_stream(**kwargs):
            return
            yield  # noqa: unreachable

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = empty_stream
        mock_adapter.close = AsyncMock()
        mock_adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test", estimated_cost_usd=Decimal("0"), estimated_credits=0,
        )
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_subscribers = AsyncMock()
        handler.on_complete = AsyncMock()
        handler._dispatch_post_tasks = MagicMock()

        await handler._stream_generate(
            task_id="t1",
            message_id="m1",
            conversation_id="c1",
            user_id="u1",
            content=[TextPart(text="hi")],
            model_id="test-model",
        )

        mock_adapter.close.assert_awaited_once()


# ============================================================
# TestStreamDirectReply
# ============================================================


class TestStreamDirectReply:

    @pytest.mark.asyncio
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_sends_start_and_chunk(self, mock_ws):
        """发送 message_start + message_chunk WS 消息"""
        handler = _make_handler()
        handler.on_complete = AsyncMock()
        mock_ws.send_to_task_subscribers = AsyncMock()

        await handler._stream_direct_reply(
            task_id="t1",
            message_id="m1",
            conversation_id="c1",
            text="直接回复内容",
        )

        # 应该发送 2 次 WS 消息（start + chunk）
        assert mock_ws.send_to_task_subscribers.await_count == 2
        # on_complete 积分=0
        handler.on_complete.assert_awaited_once()
        call_args = handler.on_complete.call_args
        assert call_args.kwargs["credits_consumed"] == 0

    @pytest.mark.asyncio
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_exception_calls_on_error(self, mock_ws):
        """异常调 on_error"""
        handler = _make_handler()
        handler.on_error = AsyncMock()
        mock_ws.send_to_task_subscribers = AsyncMock(
            side_effect=Exception("ws down"),
        )

        await handler._stream_direct_reply(
            task_id="t1",
            message_id="m1",
            conversation_id="c1",
            text="test",
        )

        handler.on_error.assert_awaited_once()
        call_args = handler.on_error.call_args
        assert call_args.kwargs["error_code"] == "DIRECT_REPLY_FAILED"


# ============================================================
# TestHandleChatCreditsOnComplete
# ============================================================


class TestHandleChatCreditsOnComplete:

    @pytest.mark.asyncio
    async def test_credits_positive_deducts(self):
        """credits>0 调 _deduct_directly"""
        handler = _make_handler()
        handler._deduct_directly = MagicMock()
        task = {"user_id": "u1", "model_id": "gemini-3-pro"}

        await handler._handle_credits_on_complete(task, credits_consumed=5)

        handler._deduct_directly.assert_called_once()
        call_args = handler._deduct_directly.call_args
        assert call_args.kwargs["amount"] == 5

    @pytest.mark.asyncio
    async def test_credits_zero_no_deduction(self):
        """credits=0 不调 _deduct_directly"""
        handler = _make_handler()
        handler._deduct_directly = MagicMock()
        task = {"user_id": "u1", "model_id": "free-model"}

        await handler._handle_credits_on_complete(task, credits_consumed=0)

        handler._deduct_directly.assert_not_called()


# ============================================================
# TestRecordBreakerResult — 熔断器记录
# ============================================================


class TestRecordBreakerResult:

    @patch("services.handlers.chat_handler.ChatHandler._record_breaker_result.__wrapped__", create=True)
    def _call(self, model_id, success, error=None):
        """直接调用静态方法"""
        ChatHandler._record_breaker_result(model_id, success=success, error=error)

    @patch("services.circuit_breaker.get_breaker")
    @patch("services.adapters.factory.MODEL_REGISTRY", {
        "gemini-3-pro": MagicMock(provider="kie"),
    })
    def test_success_records_to_breaker(self, mock_get_breaker):
        """成功时调用 breaker.record_success()"""
        mock_breaker = MagicMock()
        mock_get_breaker.return_value = mock_breaker

        ChatHandler._record_breaker_result("gemini-3-pro", success=True)

        mock_get_breaker.assert_called_once_with("kie")
        mock_breaker.record_success.assert_called_once()

    @patch("services.circuit_breaker.get_breaker")
    @patch("services.adapters.factory.MODEL_REGISTRY", {
        "gemini-3-pro": MagicMock(provider="kie"),
    })
    def test_failure_records_to_breaker(self, mock_get_breaker):
        """失败时调用 breaker.record_failure()"""
        mock_breaker = MagicMock()
        mock_get_breaker.return_value = mock_breaker

        ChatHandler._record_breaker_result(
            "gemini-3-pro", success=False, error=RuntimeError("timeout"),
        )

        mock_breaker.record_failure.assert_called_once()

    @patch("services.circuit_breaker.get_breaker")
    @patch("services.adapters.factory.MODEL_REGISTRY", {
        "gemini-3-pro": MagicMock(provider="kie"),
    })
    def test_provider_unavailable_error_skipped(self, mock_get_breaker):
        """ProviderUnavailableError 不记录失败（避免重复计入）"""
        from services.adapters.types import ProviderUnavailableError, ModelProvider

        error = ProviderUnavailableError("熔断中", provider=ModelProvider.KIE)
        ChatHandler._record_breaker_result("gemini-3-pro", success=False, error=error)

        mock_get_breaker.assert_not_called()

    @patch("services.circuit_breaker.get_breaker")
    def test_unknown_model_skipped(self, mock_get_breaker):
        """未注册模型不记录"""
        ChatHandler._record_breaker_result("nonexistent-model", success=True)

        mock_get_breaker.assert_not_called()


# ============================================================
# 性能优化相关测试
# ============================================================


class TestStreamOptimizations:

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_chunk_msg_no_accumulated_field(self, mock_ws, mock_factory):
        """build_message_chunk 不再传 accumulated（O(n²)流量优化）"""
        handler = _make_handler()
        handler._build_llm_messages = AsyncMock(return_value=[
            {"role": "user", "content": "hi"},
        ])
        handler.on_complete = AsyncMock()
        handler._dispatch_post_tasks = MagicMock()

        async def mock_stream_chat(**kwargs):
            yield StreamChunk(content="你", prompt_tokens=0, completion_tokens=0)
            yield StreamChunk(content="好", prompt_tokens=10, completion_tokens=2)

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream_chat
        mock_adapter.close = AsyncMock()
        mock_adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test", estimated_cost_usd=Decimal("0"), estimated_credits=1,
        )
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_subscribers = AsyncMock()

        await handler._stream_generate(
            task_id="t1", message_id="m1", conversation_id="c1",
            user_id="u1", content=[TextPart(text="hi")], model_id="test",
        )

        # 检查所有 WS 推送的 chunk 消息不含 accumulated
        from schemas.websocket import WSMessageType
        for call in mock_ws.send_to_task_subscribers.call_args_list:
            msg = call.args[1]
            if msg.get("type") == WSMessageType.MESSAGE_CHUNK.value:
                assert "accumulated" not in msg.get("data", {}), \
                    "chunk 消息不应包含 accumulated 字段"

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_chat_adapter")
    @patch("services.handlers.chat_handler.ws_manager")
    async def test_prefetched_summary_passed_to_build_llm_messages(self, mock_ws, mock_factory):
        """_prefetched_summary 从 _params 传递到 _build_llm_messages"""
        handler = _make_handler()

        captured_kwargs = {}
        original_build = AsyncMock(return_value=[{"role": "user", "content": "hi"}])

        async def capture_build(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return await original_build(*args, **kwargs)

        handler._build_llm_messages = capture_build
        handler.on_complete = AsyncMock()
        handler._dispatch_post_tasks = MagicMock()

        async def mock_stream_chat(**kwargs):
            yield StreamChunk(content="ok", prompt_tokens=5, completion_tokens=1)

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream_chat
        mock_adapter.close = AsyncMock()
        mock_adapter.estimate_cost_unified.return_value = CostEstimate(
            model="test", estimated_cost_usd=Decimal("0"), estimated_credits=0,
        )
        mock_factory.return_value = mock_adapter
        mock_ws.send_to_task_subscribers = AsyncMock()

        await handler._stream_generate(
            task_id="t1", message_id="m1", conversation_id="c1",
            user_id="u1", content=[TextPart(text="hi")], model_id="test",
            _params={"_prefetched_summary": "之前讨论了Python"},
        )

        assert captured_kwargs.get("prefetched_summary") == "之前讨论了Python"
