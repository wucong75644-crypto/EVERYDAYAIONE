"""
ChatHandler 智能重试单元测试

覆盖：
- smart_mode + 模型失败 → route_retry → 新模型重试 → 成功
- smart_mode + route_retry 返回 None → 报原始错误
- smart_mode + 连续失败达到上限 → 放弃
- 非 smart_mode → 不重试，直接报错
- 重试时 adapter 正确关闭和重建
- 重试时 WS 推送 retry 通知
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart
from services.intent_router import RetryContext, RoutingDecision
from services.handlers.chat_handler import ChatHandler


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_db():
    """创建 mock 数据库"""
    db = MagicMock()
    # tasks table
    task_mock = MagicMock()
    task_mock.insert.return_value = task_mock
    task_mock.update.return_value = task_mock
    task_mock.eq.return_value = task_mock
    task_mock.execute.return_value = MagicMock(data=[])
    db.table.return_value = task_mock
    return db


@pytest.fixture
def handler(mock_db):
    return ChatHandler(mock_db)


# ============================================================
# _attempt_chat_retry 单元测试
# ============================================================


class TestAttemptChatRetry:

    @pytest.mark.asyncio
    async def test_smart_mode_retry_succeeds(self, handler):
        """smart_mode + 模型失败 → route_retry 返回新模型 → 返回 True"""
        new_decision = RoutingDecision(
            generation_type=GenerationType.CHAT,
            recommended_model="gemini-3-flash",
            routed_by="model",
        )

        with patch.object(handler, "_build_retry_context") as mock_build, \
             patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=new_decision), \
             patch.object(handler, "_send_retry_notification", new_callable=AsyncMock) as mock_notify, \
             patch.object(handler, "_stream_generate", new_callable=AsyncMock) as mock_stream:

            mock_ctx = MagicMock()
            mock_ctx.can_retry = True
            mock_ctx.failed_attempts = [{"model": "gemini-3-pro", "error": "timeout"}]
            mock_build.return_value = mock_ctx

            result = await handler._attempt_chat_retry(
                error=Exception("timeout"),
                task_id="task-1",
                message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                content=[TextPart(text="你好")],
                model_id="gemini-3-pro",
                thinking_effort=None,
                thinking_mode=None,

                _params={"_is_smart_mode": True, "model": "gemini-3-pro"},
                _retry_context=None,
            )

            assert result is True
            mock_notify.assert_awaited_once_with(
                "task-1", "conv-1", "user-1", "gemini-3-flash", 1,
            )
            mock_stream.assert_awaited_once()
            call_kwargs = mock_stream.call_args.kwargs
            assert call_kwargs["model_id"] == "gemini-3-flash"

    @pytest.mark.asyncio
    async def test_smart_mode_route_retry_returns_none(self, handler):
        """smart_mode + route_retry 返回 None → 返回 False（不重试）"""
        with patch.object(handler, "_build_retry_context") as mock_build, \
             patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=None):

            mock_ctx = MagicMock()
            mock_ctx.can_retry = True
            mock_build.return_value = mock_ctx

            result = await handler._attempt_chat_retry(
                error=Exception("error"),
                task_id="task-1",
                message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                content=[TextPart(text="hello")],
                model_id="gemini-3-pro",
                thinking_effort=None,
                thinking_mode=None,

                _params={"_is_smart_mode": True},
                _retry_context=None,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_not_smart_mode_no_retry(self, handler):
        """非 smart_mode → _build_retry_context 返回 None → 不重试"""
        with patch.object(handler, "_build_retry_context", return_value=None):
            result = await handler._attempt_chat_retry(
                error=Exception("error"),
                task_id="task-1",
                message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                content=[TextPart(text="hello")],
                model_id="gemini-3-pro",
                thinking_effort=None,
                thinking_mode=None,

                _params={},
                _retry_context=None,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_max_retries_reached_no_retry(self, handler):
        """已达最大重试次数 → can_retry=False → 不重试"""
        with patch.object(handler, "_build_retry_context") as mock_build:
            mock_ctx = MagicMock()
            mock_ctx.can_retry = False
            mock_build.return_value = mock_ctx

            result = await handler._attempt_chat_retry(
                error=Exception("error"),
                task_id="task-1",
                message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                content=[TextPart(text="hello")],
                model_id="gemini-3-flash",
                thinking_effort=None,
                thinking_mode=None,

                _params={"_is_smart_mode": True},
                _retry_context=MagicMock(),
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_adapter_closed_on_retry(self, handler):
        """重试时旧 adapter 应被关闭"""
        new_decision = RoutingDecision(
            generation_type=GenerationType.CHAT,
            recommended_model="gemini-3-flash",
            routed_by="model",
        )
        mock_adapter = AsyncMock()
        handler._adapter = mock_adapter

        with patch.object(handler, "_build_retry_context") as mock_build, \
             patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=new_decision), \
             patch.object(handler, "_send_retry_notification", new_callable=AsyncMock), \
             patch.object(handler, "_stream_generate", new_callable=AsyncMock):

            mock_ctx = MagicMock()
            mock_ctx.can_retry = True
            mock_ctx.failed_attempts = [{"model": "gemini-3-pro", "error": "err"}]
            mock_build.return_value = mock_ctx

            await handler._attempt_chat_retry(
                error=Exception("error"),
                task_id="task-1",
                message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                content=[TextPart(text="hello")],
                model_id="gemini-3-pro",
                thinking_effort=None,
                thinking_mode=None,

                _params={"_is_smart_mode": True},
                _retry_context=None,
            )

        mock_adapter.close.assert_awaited_once()
        assert handler._adapter is None

    @pytest.mark.asyncio
    async def test_db_model_updated_on_retry(self, handler, mock_db):
        """重试时数据库中的 model_id 应被更新"""
        new_decision = RoutingDecision(
            generation_type=GenerationType.CHAT,
            recommended_model="gemini-3-flash",
            routed_by="model",
        )

        with patch.object(handler, "_build_retry_context") as mock_build, \
             patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=new_decision), \
             patch.object(handler, "_send_retry_notification", new_callable=AsyncMock), \
             patch.object(handler, "_stream_generate", new_callable=AsyncMock):

            mock_ctx = MagicMock()
            mock_ctx.can_retry = True
            mock_ctx.failed_attempts = [{"model": "gemini-3-pro", "error": "err"}]
            mock_build.return_value = mock_ctx

            await handler._attempt_chat_retry(
                error=Exception("error"),
                task_id="task-1",
                message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                content=[TextPart(text="hello")],
                model_id="gemini-3-pro",
                thinking_effort=None,
                thinking_mode=None,

                _params={"_is_smart_mode": True},
                _retry_context=None,
            )

        # 验证 DB update 调用
        mock_db.table.assert_any_call("tasks")

    @pytest.mark.asyncio
    async def test_retry_passes_context_to_stream_generate(self, handler):
        """重试时 retry_context 应传递给 _stream_generate"""
        new_decision = RoutingDecision(
            generation_type=GenerationType.CHAT,
            recommended_model="gemini-3-flash",
            routed_by="model",
        )

        mock_ctx = MagicMock()
        mock_ctx.can_retry = True
        mock_ctx.failed_attempts = [{"model": "gemini-3-pro", "error": "err"}]

        with patch.object(handler, "_build_retry_context", return_value=mock_ctx), \
             patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=new_decision), \
             patch.object(handler, "_send_retry_notification", new_callable=AsyncMock), \
             patch.object(handler, "_stream_generate", new_callable=AsyncMock) as mock_stream:

            await handler._attempt_chat_retry(
                error=Exception("error"),
                task_id="task-1",
                message_id="msg-1",
                conversation_id="conv-1",
                user_id="user-1",
                content=[TextPart(text="hello")],
                model_id="gemini-3-pro",
                thinking_effort=None,
                thinking_mode=None,

                _params={"_is_smart_mode": True},
                _retry_context=None,
            )

            call_kwargs = mock_stream.call_args.kwargs
            assert call_kwargs["_retry_context"] is mock_ctx


# ============================================================
# BaseHandler._build_retry_context 单元测试
# ============================================================


class TestBuildRetryContext:

    @pytest.fixture
    def handler(self, mock_db):
        return ChatHandler(mock_db)

    def test_non_smart_returns_none(self, handler):
        """非 smart_mode → 返回 None"""
        result = handler._build_retry_context(
            params={},
            content=[TextPart(text="hello")],
            model_id="gemini-3-pro",
            error="timeout",
        )
        assert result is None

    def test_smart_mode_creates_context(self, handler):
        """smart_mode → 创建 RetryContext"""
        result = handler._build_retry_context(
            params={"_is_smart_mode": True},
            content=[TextPart(text="hello")],
            model_id="gemini-3-pro",
            error="timeout",
        )
        assert result is not None
        assert result.is_smart_mode is True
        assert result.original_content == "hello"
        assert result.generation_type == GenerationType.CHAT
        assert len(result.failed_attempts) == 1
        assert result.failed_attempts[0]["model"] == "gemini-3-pro"

    def test_existing_context_updated(self, handler):
        """已有 context → 追加失败记录"""
        existing = RetryContext(
            is_smart_mode=True,
            original_content="hello",
            generation_type=GenerationType.CHAT,
        )
        existing.add_failure("model-a", "error-1")

        result = handler._build_retry_context(
            params={"_is_smart_mode": True},
            content=[TextPart(text="hello")],
            model_id="model-b",
            error="error-2",
            existing_ctx=existing,
        )
        assert result is existing
        assert len(result.failed_attempts) == 2
        assert result.failed_models == ["model-a", "model-b"]
