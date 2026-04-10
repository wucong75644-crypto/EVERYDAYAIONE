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
    async def test_subscribe_model_with_slash_unit(self, service, current_user):
        """含斜杠的 OpenRouter 模型 ID — service 层"""
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


# ============================================================
# HTTP 路由层集成测试（TestClient — 真 HTTP 调用）
#
# 重要：这一层覆盖 path 参数解析、URL 解码、路由匹配等
# Starlette/FastAPI 路由层行为，单元测试直接 import 函数无法覆盖。
# 例如 model_id 含斜杠（openai/gpt-4.1）必须通过 TestClient 才能验证。
# ============================================================

class TestRoutingIntegration:
    """订阅路由 HTTP 集成测试 — 用 TestClient 真 HTTP 调用"""

    def _build_app(self, service):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routes.subscription import router, get_subscription_service
        from api.deps import get_current_user

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: {"id": "user-123", "role": "user"}
        app.dependency_overrides[get_subscription_service] = lambda: service
        return TestClient(app)

    def test_subscribe_no_slash_via_http(self, service):
        """无斜杠的 model_id 走 HTTP 能正常订阅"""
        client = self._build_app(service)
        resp = client.post("/subscriptions/glm-5")
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "glm-5"

    def test_subscribe_with_slash_encoded_via_http(self, service):
        """含斜杠的 model_id（encoded %2F）走 HTTP 能正常订阅 —— 防回归"""
        client = self._build_app(service)
        resp = client.post("/subscriptions/openai%2Fgpt-4.1")
        assert resp.status_code == 200, f"路由 404 说明 path converter 没用 :path"
        assert resp.json()["model_id"] == "openai/gpt-4.1"

    def test_subscribe_with_slash_raw_via_http(self, service):
        """含斜杠的 model_id（raw /）走 HTTP 能正常订阅 —— 防回归"""
        client = self._build_app(service)
        resp = client.post("/subscriptions/anthropic/claude-sonnet-4.6")
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "anthropic/claude-sonnet-4.6"

    def test_subscribe_image_model_with_slash_via_http(self, service):
        """图片模型含斜杠 ID 走 HTTP 能正常订阅 —— 防回归"""
        client = self._build_app(service)
        resp = client.post("/subscriptions/google%2Fnano-banana")
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "google/nano-banana"

    def test_unsubscribe_with_slash_via_http(self, mock_db, service):
        """含斜杠的 model_id 走 HTTP 能正常取消订阅 —— 防回归"""
        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "user-123", "model_id": "openai/gpt-5.4"},
        ])
        client = self._build_app(service)
        resp = client.delete("/subscriptions/openai%2Fgpt-5.4")
        assert resp.status_code == 200
        assert resp.json()["model_id"] == "openai/gpt-5.4"
