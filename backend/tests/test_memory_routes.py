"""
记忆 API 路由单元测试

覆盖所有记忆 API 端点的请求处理、参数校验和异常传递。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from tests.conftest import MockSupabaseClient


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    """Mock 同步 Supabase 客户端"""
    return MockSupabaseClient()


@pytest.fixture
def user_id():
    return str(uuid4())


@pytest.fixture
def mock_ctx(user_id):
    """Mock OrgContext（散客模式）"""
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.org_id = None
    ctx.org_role = None
    return ctx


@pytest.fixture
def mock_memory_service():
    """Mock MemoryService 实例"""
    service = AsyncMock()
    service.get_settings = AsyncMock(return_value={
        "memory_enabled": True,
        "retention_days": 7,
        "updated_at": "2026-01-01T00:00:00Z",
    })
    service.update_settings = AsyncMock(return_value={
        "memory_enabled": False,
        "retention_days": 30,
        "updated_at": "2026-03-06T00:00:00Z",
    })
    service.get_all_memories = AsyncMock(return_value=[
        {
            "id": "mem-1",
            "memory": "用户是程序员",
            "metadata": {"source": "auto", "conversation_id": None},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": None,
        }
    ])
    service.add_memory = AsyncMock(return_value=[
        {
            "id": "mem-new",
            "memory": "新记忆",
            "metadata": {"source": "manual", "conversation_id": None},
            "created_at": "2026-03-06T00:00:00Z",
            "updated_at": None,
        }
    ])
    service.update_memory = AsyncMock(return_value={
        "id": "mem-1",
        "memory": "更新后的记忆",
        "updated_at": "2026-03-06T12:00:00Z",
    })
    service.delete_memory = AsyncMock(return_value=None)
    service.delete_all_memories = AsyncMock(return_value=None)
    return service


# ============ 设置接口测试 ============


class TestMemorySettingsRoutes:
    """记忆设置路由测试"""

    @pytest.mark.asyncio
    async def test_get_settings(self, mock_memory_service, mock_ctx, user_id):
        """GET /api/memories/settings 返回用户设置"""
        from api.routes.memory import get_memory_settings

        result = await get_memory_settings(
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["memory_enabled"] is True
        assert result["retention_days"] == 7
        mock_memory_service.get_settings.assert_awaited_once_with(user_id)

    @pytest.mark.asyncio
    async def test_get_settings_error(self, mock_memory_service, mock_ctx):
        """GET /api/memories/settings 异常时抛出 AppException"""
        from api.routes.memory import get_memory_settings
        from core.exceptions import AppException

        mock_memory_service.get_settings.side_effect = Exception("DB error")

        with pytest.raises(AppException) as exc_info:
            await get_memory_settings(
                ctx=mock_ctx,
                service=mock_memory_service,
            )

        assert exc_info.value.code == "ROUTE_GET_MEMORY_SETTINGS_ERROR"

    @pytest.mark.asyncio
    async def test_update_settings(self, mock_memory_service, mock_ctx, user_id):
        """PUT /api/memories/settings 更新设置"""
        from api.routes.memory import update_memory_settings
        from schemas.memory import MemorySettingsUpdateRequest

        body = MemorySettingsUpdateRequest(
            memory_enabled=False, retention_days=30
        )

        result = await update_memory_settings(
            body=body,
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["memory_enabled"] is False
        assert result["retention_days"] == 30
        mock_memory_service.update_settings.assert_awaited_once_with(
            user_id, memory_enabled=False, retention_days=30
        )

    @pytest.mark.asyncio
    async def test_update_settings_app_exception_passthrough(
        self, mock_memory_service, mock_ctx,
    ):
        """PUT /api/memories/settings AppException 直接传递"""
        from api.routes.memory import update_memory_settings
        from core.exceptions import AppException
        from schemas.memory import MemorySettingsUpdateRequest

        mock_memory_service.update_settings.side_effect = AppException(
            code="MEMORY_SETTINGS_UPDATE_ERROR",
            message="更新失败",
            status_code=500,
        )

        body = MemorySettingsUpdateRequest(memory_enabled=True)

        with pytest.raises(AppException) as exc_info:
            await update_memory_settings(
                body=body,
                ctx=mock_ctx,
                service=mock_memory_service,
            )

        assert exc_info.value.code == "MEMORY_SETTINGS_UPDATE_ERROR"


# ============ 记忆 CRUD 路由测试 ============


class TestMemoryCRUDRoutes:
    """记忆 CRUD 路由测试"""

    @pytest.mark.asyncio
    async def test_get_memories(self, mock_memory_service, user_id, mock_ctx):
        """GET /api/memories 返回记忆列表"""
        from api.routes.memory import get_memories

        result = await get_memories(
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["total"] == 1
        assert len(result["memories"]) == 1
        assert result["memories"][0]["id"] == "mem-1"

    @pytest.mark.asyncio
    async def test_get_memories_empty(self, mock_memory_service, user_id, mock_ctx):
        """GET /api/memories 无记忆时返回空列表"""
        from api.routes.memory import get_memories

        mock_memory_service.get_all_memories.return_value = []

        result = await get_memories(
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["total"] == 0
        assert result["memories"] == []

    @pytest.mark.asyncio
    async def test_add_memory(self, mock_memory_service, user_id, mock_ctx):
        """POST /api/memories 添加记忆"""
        from api.routes.memory import add_memory
        from schemas.memory import MemoryAddRequest

        body = MemoryAddRequest(content="我喜欢Python")

        result = await add_memory(
            body=body,
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["count"] == 1
        assert result["memories"][0]["id"] == "mem-new"
        mock_memory_service.add_memory.assert_awaited_once_with(
            user_id=user_id, content="我喜欢Python", source="manual", org_id=None
        )

    @pytest.mark.asyncio
    async def test_add_memory_limit_reached(self, mock_memory_service, user_id, mock_ctx):
        """POST /api/memories 达到上限时传递 AppException"""
        from api.routes.memory import add_memory
        from core.exceptions import AppException
        from schemas.memory import MemoryAddRequest

        mock_memory_service.add_memory.side_effect = AppException(
            code="MEMORY_LIMIT_REACHED",
            message="上限已达",
            status_code=400,
        )

        body = MemoryAddRequest(content="新记忆")

        with pytest.raises(AppException) as exc_info:
            await add_memory(
                body=body,
                ctx=mock_ctx,
                service=mock_memory_service,
            )

        assert exc_info.value.code == "MEMORY_LIMIT_REACHED"
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_memory(self, mock_memory_service, user_id, mock_ctx):
        """PUT /api/memories/{id} 更新记忆"""
        from api.routes.memory import update_memory
        from schemas.memory import MemoryUpdateRequest

        body = MemoryUpdateRequest(content="更新后的记忆")

        result = await update_memory(
            memory_id="mem-1",
            body=body,
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["id"] == "mem-1"
        assert result["memory"] == "更新后的记忆"

    @pytest.mark.asyncio
    async def test_delete_memory(self, mock_memory_service, user_id, mock_ctx):
        """DELETE /api/memories/{id} 删除记忆"""
        from api.routes.memory import delete_memory

        result = await delete_memory(
            memory_id="mem-1",
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["message"] == "记忆已删除"
        mock_memory_service.delete_memory.assert_awaited_once_with(
            memory_id="mem-1", user_id=user_id, org_id=None
        )

    @pytest.mark.asyncio
    async def test_delete_memory_error(self, mock_memory_service, user_id, mock_ctx):
        """DELETE /api/memories/{id} 异常时抛出 AppException"""
        from api.routes.memory import delete_memory
        from core.exceptions import AppException

        mock_memory_service.delete_memory.side_effect = Exception("unexpected")

        with pytest.raises(AppException) as exc_info:
            await delete_memory(
                memory_id="mem-1",
                ctx=mock_ctx,
                service=mock_memory_service,
            )

        assert exc_info.value.code == "ROUTE_DELETE_MEMORY_ERROR"

    @pytest.mark.asyncio
    async def test_delete_all_memories(self, mock_memory_service, user_id, mock_ctx):
        """DELETE /api/memories 清空所有记忆"""
        from api.routes.memory import delete_all_memories

        result = await delete_all_memories(
            ctx=mock_ctx,
            service=mock_memory_service,
        )

        assert result["message"] == "所有记忆已清空"
        mock_memory_service.delete_all_memories.assert_awaited_once_with(
            user_id, org_id=None
        )

    @pytest.mark.asyncio
    async def test_delete_all_memories_error(self, mock_memory_service, user_id, mock_ctx):
        """DELETE /api/memories 异常时抛出 AppException"""
        from api.routes.memory import delete_all_memories
        from core.exceptions import AppException

        mock_memory_service.delete_all_memories.side_effect = Exception("fail")

        with pytest.raises(AppException) as exc_info:
            await delete_all_memories(
                ctx=mock_ctx,
                service=mock_memory_service,
            )

        assert exc_info.value.code == "ROUTE_DELETE_ALL_MEMORIES_ERROR"
