"""
模型列表路由单元测试

覆盖模型 API 端点：
- GET /models（获取所有模型列表，公开接口）
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from tests.conftest import MockSupabaseClient
from services.subscription_service import SubscriptionService, KNOWN_MODEL_IDS


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    return MockSupabaseClient()


@pytest.fixture
def service(mock_db):
    return SubscriptionService(mock_db)


# ============================================================
# GET /models
# ============================================================

class TestGetModels:
    """获取模型列表路由测试"""

    @pytest.mark.asyncio
    async def test_returns_all_models(self, service):
        from api.routes.models import get_models

        result = await get_models(service=service)

        assert "models" in result
        assert len(result["models"]) == len(KNOWN_MODEL_IDS)

    @pytest.mark.asyncio
    async def test_models_have_correct_structure(self, service):
        from api.routes.models import get_models

        result = await get_models(service=service)

        for model in result["models"]:
            assert "id" in model
            assert "status" in model
            assert model["status"] == "active"
            assert "is_default" not in model

    @pytest.mark.asyncio
    async def test_no_auth_required(self, service):
        """公开接口，无需 current_user 参数"""
        from api.routes.models import get_models

        # 路由函数签名不包含 current_user，直接调用即可
        result = await get_models(service=service)
        assert len(result["models"]) > 0
