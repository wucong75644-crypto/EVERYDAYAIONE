"""
订阅路由单元测试

覆盖订阅管理 API 端点：
- GET /subscriptions（获取用户订阅列表）
- POST /subscriptions/{model_id}（订阅模型）
- DELETE /subscriptions/{model_id}（取消订阅）
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import MagicMock

from tests.conftest import MockSupabaseClient
from services.subscription_service import SubscriptionService


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    return MockSupabaseClient()


@pytest.fixture
def service(mock_db):
    return SubscriptionService(mock_db)


@pytest.fixture
def current_user():
    return {"id": "user-123", "role": "user"}


# ============================================================
# GET /subscriptions
# ============================================================

class TestGetSubscriptions:
    """获取订阅列表路由测试"""

    @pytest.mark.asyncio
    async def test_returns_subscriptions(self, mock_db, service, current_user):
        from api.routes.subscription import get_subscriptions

        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "user-123", "model_id": "gemini-3-flash", "subscribed_at": "2026-03-10T00:00:00Z"},
            {"user_id": "user-123", "model_id": "deepseek-v3.2", "subscribed_at": "2026-03-10T01:00:00Z"},
        ])

        result = await get_subscriptions(
            current_user=current_user,
            service=service,
        )

        assert len(result["subscriptions"]) == 2
        assert result["subscriptions"][0]["model_id"] == "gemini-3-flash"

    @pytest.mark.asyncio
    async def test_returns_empty_for_new_user(self, service, current_user):
        from api.routes.subscription import get_subscriptions

        result = await get_subscriptions(
            current_user=current_user,
            service=service,
        )

        assert result["subscriptions"] == []


# ============================================================
# POST /subscriptions/{model_id}
# ============================================================

class TestSubscribeModel:
    """订阅模型路由测试"""

    @pytest.mark.asyncio
    async def test_subscribe_success(self, service, current_user):
        from api.routes.subscription import subscribe_model

        result = await subscribe_model(
            model_id="gemini-3-flash",
            current_user=current_user,
            service=service,
        )

        assert result["message"] == "订阅成功"
        assert result["model_id"] == "gemini-3-flash"

    @pytest.mark.asyncio
    async def test_subscribe_model_with_slash(self, service, current_user):
        """含斜杠的 OpenRouter 模型 ID"""
        from api.routes.subscription import subscribe_model

        result = await subscribe_model(
            model_id="openai/gpt-5.4",
            current_user=current_user,
            service=service,
        )

        assert result["model_id"] == "openai/gpt-5.4"

    @pytest.mark.asyncio
    async def test_subscribe_unknown_model(self, service, current_user):
        """未知模型返回 ValidationError"""
        from api.routes.subscription import subscribe_model
        from core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="未知的模型"):
            await subscribe_model(
                model_id="nonexistent",
                current_user=current_user,
                service=service,
            )


# ============================================================
# DELETE /subscriptions/{model_id}
# ============================================================

class TestUnsubscribeModel:
    """取消订阅路由测试"""

    @pytest.mark.asyncio
    async def test_unsubscribe_success(self, mock_db, service, current_user):
        from api.routes.subscription import unsubscribe_model

        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "user-123", "model_id": "deepseek-r1"},
        ])

        result = await unsubscribe_model(
            model_id="deepseek-r1",
            current_user=current_user,
            service=service,
        )

        assert result["message"] == "已取消订阅"
        assert result["model_id"] == "deepseek-r1"

    @pytest.mark.asyncio
    async def test_unsubscribe_not_subscribed(self, service, current_user):
        """未订阅的模型取消时抛 NotFoundError"""
        from api.routes.subscription import unsubscribe_model
        from core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await unsubscribe_model(
                model_id="kimi-k2.5",
                current_user=current_user,
                service=service,
            )

    @pytest.mark.asyncio
    async def test_unsubscribe_any_model_allowed(self, mock_db, service, current_user):
        """所有模型都可以取消订阅（无默认模型限制）"""
        from api.routes.subscription import unsubscribe_model

        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "user-123", "model_id": "gemini-3-flash"},
        ])

        result = await unsubscribe_model(
            model_id="gemini-3-flash",
            current_user=current_user,
            service=service,
        )

        assert result["message"] == "已取消订阅"
