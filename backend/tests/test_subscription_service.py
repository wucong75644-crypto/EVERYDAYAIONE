"""
subscription_service 单元测试

测试订阅管理服务的核心功能：
- 获取模型列表（从代码常量）
- 获取用户订阅列表
- 订阅模型（正常/幂等/未知模型）
- 取消订阅（正常/未订阅）
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import MagicMock

from services.subscription_service import SubscriptionService, KNOWN_MODEL_IDS
from core.exceptions import NotFoundError, ValidationError


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    """使用 conftest 的 MockSupabaseClient"""
    from tests.conftest import MockSupabaseClient
    return MockSupabaseClient()


@pytest.fixture
def service(mock_db):
    return SubscriptionService(mock_db)


# ============================================================
# TestGetAllModels
# ============================================================

class TestGetAllModels:
    """获取模型列表测试（从代码常量生成）"""

    def test_returns_all_known_models(self, service):
        result = service.get_all_models()
        assert len(result) == len(KNOWN_MODEL_IDS)

    def test_all_models_have_active_status(self, service):
        result = service.get_all_models()
        for m in result:
            assert m["status"] == "active"

    def test_models_have_id_and_status_only(self, service):
        result = service.get_all_models()
        for m in result:
            assert "id" in m
            assert "status" in m
            assert "is_default" not in m


# ============================================================
# TestGetUserSubscriptions
# ============================================================

class TestGetUserSubscriptions:
    """获取用户订阅列表测试"""

    def test_returns_subscriptions(self, mock_db, service):
        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "u1", "model_id": "gemini-3-flash", "subscribed_at": "2026-03-10T00:00:00Z"},
            {"user_id": "u1", "model_id": "gemini-3-pro", "subscribed_at": "2026-03-10T00:00:00Z"},
            {"user_id": "u2", "model_id": "deepseek-v3.2", "subscribed_at": "2026-03-10T00:00:00Z"},
        ])
        result = service.get_user_subscriptions("u1")
        assert len(result) == 2
        assert all(s["user_id"] == "u1" for s in result)

    def test_returns_empty_for_no_subscriptions(self, service):
        result = service.get_user_subscriptions("u_new")
        assert result == []

    def test_raises_on_db_error(self):
        db = MagicMock()
        db.table.side_effect = Exception("DB error")
        svc = SubscriptionService(db)
        with pytest.raises(Exception, match="DB error"):
            svc.get_user_subscriptions("u1")


# ============================================================
# TestSubscribe
# ============================================================

class TestSubscribe:
    """订阅模型测试"""

    def test_subscribe_success(self, mock_db, service):
        result = service.subscribe("u1", "gemini-3-flash")
        assert result["message"] == "订阅成功"
        assert result["model_id"] == "gemini-3-flash"

    def test_subscribe_idempotent(self, mock_db, service):
        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "u1", "model_id": "gemini-3-flash"},
        ])
        result = service.subscribe("u1", "gemini-3-flash")
        assert result["message"] == "订阅成功"

    def test_subscribe_unknown_model_raises(self, service):
        with pytest.raises(ValidationError, match="未知的模型"):
            service.subscribe("u1", "nonexistent-model")

    def test_subscribe_model_with_slash(self, mock_db, service):
        """含斜杠的模型 ID（OpenRouter 模型）"""
        result = service.subscribe("u1", "openai/gpt-5.4")
        assert result["model_id"] == "openai/gpt-5.4"

    def test_subscribe_db_insert_error_raises(self, mock_db, service):
        """insert 异常时抛出 ValidationError"""
        original_table = mock_db.table

        def patched_table(name):
            tbl = original_table(name)
            if name == "user_subscriptions":
                orig_insert = tbl.insert
                def failing_insert(data):
                    result = orig_insert(data)
                    result.execute = MagicMock(side_effect=Exception("insert failed"))
                    return result
                tbl.insert = failing_insert
            return tbl

        mock_db.table = patched_table

        with pytest.raises(ValidationError, match="订阅失败"):
            service.subscribe("u1", "gemini-3-flash")


# ============================================================
# TestUnsubscribe
# ============================================================

class TestUnsubscribe:
    """取消订阅测试"""

    def test_unsubscribe_success(self, mock_db, service):
        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "u1", "model_id": "deepseek-r1"},
        ])
        result = service.unsubscribe("u1", "deepseek-r1")
        assert result["message"] == "已取消订阅"
        assert result["model_id"] == "deepseek-r1"

    def test_unsubscribe_any_subscribed_model(self, mock_db, service):
        """任何已订阅的模型都可以取消"""
        mock_db.set_table_data("user_subscriptions", [
            {"user_id": "u1", "model_id": "gemini-3-flash"},
        ])
        result = service.unsubscribe("u1", "gemini-3-flash")
        assert result["message"] == "已取消订阅"

    def test_unsubscribe_not_subscribed_raises(self, mock_db, service):
        """未订阅的模型取消时抛 NotFoundError"""
        with pytest.raises(NotFoundError):
            service.unsubscribe("u1", "kimi-k2.5")


# ============================================================
# TestKnownModelIds
# ============================================================

class TestKnownModelIds:
    """已知模型 ID 列表测试"""

    def test_known_ids_not_empty(self):
        assert len(KNOWN_MODEL_IDS) > 0

    def test_auto_not_in_known_ids(self):
        assert "auto" not in KNOWN_MODEL_IDS

    def test_contains_key_models(self):
        assert "gemini-3-flash" in KNOWN_MODEL_IDS
        assert "openai/gpt-5.4" in KNOWN_MODEL_IDS
        assert "deepseek-v3.2" in KNOWN_MODEL_IDS
