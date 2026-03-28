"""
message_service 单元测试

测试消息服务的核心功能：
- 获取消息列表
- 获取单条消息
- 删除消息
- 获取对话历史
"""

import sys
from pathlib import Path

# Python path fix: 避免与根目录的 tests/ 冲突
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from services.message_service import MessageService
from core.exceptions import NotFoundError, PermissionDeniedError

# 测试辅助函数（避免导入冲突）
def create_test_user(
    user_id: str = None,
    phone: str = "13800138000",
    nickname: str = "测试用户",
    credits: int = 100,
    status: str = "active",
    role: str = "user",
    password_hash: str = None,
) -> dict:
    """创建测试用户数据"""
    from datetime import datetime, timezone
    return {
        "id": user_id or str(uuid4()),
        "phone": phone,
        "nickname": nickname,
        "credits": credits,
        "status": status,
        "role": role,
        "password_hash": password_hash,
        "avatar_url": None,
        "login_methods": ["phone"],
        "created_by": "phone",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }


def create_test_message(
    message_id: str = None,
    conversation_id: str = None,
    role: str = "user",
    content: str = "测试消息",
    credits_cost: int = 0,
) -> dict:
    """创建测试消息数据"""
    from datetime import datetime, timezone
    return {
        "id": message_id or str(uuid4()),
        "conversation_id": conversation_id or str(uuid4()),
        "role": role,
        "content": content,
        "image_url": None,
        "video_url": None,
        "credits_cost": credits_cost,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def create_test_conversation(
    conversation_id: str = None,
    user_id: str = None,
    title: str = "测试对话",
) -> dict:
    """创建测试对话数据"""
    from datetime import datetime, timezone
    return {
        "id": conversation_id or str(uuid4()),
        "user_id": user_id or str(uuid4()),
        "title": title,
        "model_id": "gpt-4",
        "last_message": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class TestMessageServiceGet:
    """消息查询测试"""

    @pytest.fixture
    def message_service(self, mock_db):
        return MessageService(mock_db)

    @pytest.mark.asyncio
    async def test_get_messages_success(self, message_service, mock_db):
        """测试：获取消息列表"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        messages = [
            create_test_message(conversation_id=conversation["id"], content="消息1"),
            create_test_message(conversation_id=conversation["id"], content="消息2"),
        ]
        mock_db.set_table_data("messages", messages)

        # Mock 链式调用
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=messages)
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            # Act
            result = await message_service.get_messages(
                conversation_id=conversation["id"],
                user_id=user["id"],
                limit=50
            )

        # Assert
        assert "messages" in result
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_get_message_success(self, message_service, mock_db):
        """测试：获取单条消息"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        message = create_test_message(conversation_id=conversation["id"])

        # Mock 链式调用
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.single.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=message)
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            # Act
            result = await message_service.get_message(
                conversation_id=conversation["id"],
                message_id=message["id"],
                user_id=user["id"]
            )

        # Assert
        assert result["id"] == message["id"]

    @pytest.mark.asyncio
    async def test_get_message_not_found(self, message_service, mock_db):
        """测试：消息不存在"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.single.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=None)
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            # Act & Assert
            with pytest.raises(NotFoundError):
                await message_service.get_message(
                    conversation_id=conversation["id"],
                    message_id="nonexistent",
                    user_id=user["id"]
                )


class TestMessageServiceDelete:
    """消息删除测试"""

    @pytest.fixture
    def message_service(self, mock_db):
        return MessageService(mock_db)

    @pytest.mark.asyncio
    async def test_delete_message_success(self, message_service, mock_db):
        """测试：删除消息成功"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        message = create_test_message(conversation_id=conversation["id"])

        # Mock select 查询
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[message])
        mock_query.delete.return_value = mock_query
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            # Act
            result = await message_service.delete_message(
                message_id=message["id"],
                user_id=user["id"]
            )

        # Assert
        assert result["id"] == message["id"]
        assert result["conversation_id"] == conversation["id"]

    @pytest.mark.asyncio
    async def test_delete_message_not_found(self, message_service, mock_db):
        """测试：删除不存在的消息"""
        # Arrange
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[])
        mock_db.table = MagicMock(return_value=mock_query)

        # Act & Assert
        with pytest.raises(NotFoundError):
            await message_service.delete_message(
                message_id="nonexistent",
                user_id="user_123"
            )

    @pytest.mark.asyncio
    async def test_delete_message_other_user_rejected(self, message_service, mock_db):
        """测试：其他用户删除消息时，get_conversation SQL 层过滤返回 NotFoundError"""
        # Arrange
        owner = create_test_user(user_id="owner_123")
        other_user = create_test_user(user_id="other_123")
        conversation = create_test_conversation(user_id=owner["id"])
        message = create_test_message(conversation_id=conversation["id"])

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[message])
        mock_db.table = MagicMock(return_value=mock_query)

        # get_conversation 在 SQL 层过滤 user_id，其他用户查不到 → NotFoundError
        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            side_effect=NotFoundError("对话", conversation["id"])
        ):
            with pytest.raises(NotFoundError):
                await message_service.delete_message(
                    message_id=message["id"],
                    user_id=other_user["id"]
                )


class TestMessageServiceHistory:
    """消息历史测试"""

    @pytest.fixture
    def message_service(self, mock_db):
        return MessageService(mock_db)

    @pytest.mark.asyncio
    async def test_get_conversation_history(self, message_service, mock_db):
        """测试：获取对话历史（AI 上下文）"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        messages = [
            create_test_message(
                conversation_id=conversation["id"],
                role="user",
                content="你好"
            ),
            create_test_message(
                conversation_id=conversation["id"],
                role="assistant",
                content="你好，有什么可以帮你的？"
            ),
        ]

        with patch.object(
            message_service,
            "get_messages",
            return_value={"messages": messages, "total": 2}
        ):
            # Act
            history = await message_service._get_conversation_history(
                conversation_id=conversation["id"],
                user_id=user["id"],
                limit=10
            )

        # Assert
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
