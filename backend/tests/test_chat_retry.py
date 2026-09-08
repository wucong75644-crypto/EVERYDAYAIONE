"""Chat 复用 BaseHandler 策略，Gateway 接管模型 attempt。"""
from unittest.mock import AsyncMock, MagicMock, Mock
import pytest
from schemas.message import TextPart, GenerationType
from services.handlers.chat_handler import ChatHandler
from services.intent_router import RetryContext, RoutingDecision

@pytest.fixture
def mock_db():
    return MagicMock()

@pytest.fixture
def handler(mock_db):
    return ChatHandler(mock_db)

@pytest.mark.asyncio
async def test_policy_reuses_base_handler_context_and_router(handler):
    handler._build_retry_context = Mock(wraps=handler._build_retry_context)
    handler._route_retry = AsyncMock(return_value=RoutingDecision(
        generation_type=GenerationType.CHAT, recommended_model="gemini-3-flash",
    ))
    policy = handler._build_model_retry_policy(
        params={"_is_smart_mode": True}, content=[TextPart(text="hello")],
        task_id="t1", conversation_id="c1", user_id="u1",
    )
    ctx = policy.build_context("gemini-3-pro", ConnectionError("down"), None)
    decision = await policy.route(ctx)
    handler._build_retry_context.assert_called_once()
    handler._route_retry.assert_awaited_once_with(ctx)
    assert ctx.failed_models == ["gemini-3-pro"]
    assert decision.recommended_model == "gemini-3-flash"

@pytest.mark.asyncio
async def test_web_retry_hook_keeps_notification_and_model_metadata(handler, mock_db):
    handler._send_retry_notification = AsyncMock()
    policy = handler._build_model_retry_policy(
        params={}, content=[], task_id="t1", conversation_id="c1", user_id="u1",
    )
    await policy.on_retry("gemini-3-flash", 1)
    handler._send_retry_notification.assert_awaited_once_with(
        "t1", "c1", "u1", "gemini-3-flash", 1,
    )
    mock_db.table.return_value.update.assert_called_once_with({"model_id": "gemini-3-flash"})
    mock_db.table.return_value.update.return_value.eq.assert_called_once_with("external_task_id", "t1")

@pytest.mark.asyncio
async def test_actor_retry_hook_uses_owned_callback_without_web_write(handler, mock_db):
    actor_hook = AsyncMock()
    handler._send_retry_notification = AsyncMock()
    policy = handler._build_model_retry_policy(
        params={}, content=[], task_id="t1", conversation_id="c1", user_id="u1",
        on_retry=actor_hook,
    )
    await policy.on_retry("gemini-3-flash", 1)
    actor_hook.assert_awaited_once_with("gemini-3-flash", 1)
    handler._send_retry_notification.assert_not_awaited()
    mock_db.table.assert_not_called()

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
