"""模型目录的 HTTP 契约：匿名可读公开字段，用户订阅仍需认证。"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.routes import models, subscription
from core.database import get_db
from core.exceptions import AppException
from services.subscription_service import KNOWN_MODEL_IDS, SubscriptionService


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(models.router, prefix="/api")
    app.include_router(subscription.router, prefix="/api")

    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    def unexpected_database_access():
        pytest.fail("公开模型目录和匿名认证拒绝不应访问数据库")

    # Keep the actual auth and service dependencies: overriding those would hide
    # the indirect ScopedDB -> get_current_user_id regression.
    app.dependency_overrides[get_db] = unexpected_database_access
    with TestClient(app) as test_client:
        yield test_client


def test_anonymous_models_are_public_and_database_independent(client):
    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"models"}
    assert {model["id"] for model in payload["models"]} == KNOWN_MODEL_IDS
    for model in payload["models"]:
        assert set(model) == {"id", "status"}
        assert model["status"] == "active"


def test_model_response_filters_non_public_fields(client):
    with patch.object(SubscriptionService, "get_all_models", return_value=[{
        "id": "gemini-3-flash", "status": "active", "internal_config": "private",
    }]):
        response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json() == {"models": [{"id": "gemini-3-flash", "status": "active"}]}


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/subscriptions"),
    ("POST", "/api/subscriptions/gemini-3-flash"),
    ("DELETE", "/api/subscriptions/gemini-3-flash"),
])
def test_anonymous_subscriptions_remain_protected(client, method, path):
    response = client.request(method, path)

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_ERROR"
