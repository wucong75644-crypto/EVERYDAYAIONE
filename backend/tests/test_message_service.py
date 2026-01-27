"""
message_service 单元测试

测试消息服务的核心功能：
- 创建消息
- 获取消息列表
- 获取单条消息
- 删除消息
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from services.message_service import MessageService
from core.exceptions import NotFoundError, PermissionDeniedError
from tests.conftest import create_test_user, create_test_message, create_test_conversation


class TestMessageServiceCreate:
    """消息创建测试"""

    @pytest.fixture
    def message_service(self, mock_db):
        return MessageService(mock_db)

    @pytest.mark.asyncio
    async def test_create_message_success(self, message_service, mock_db):
        """测试：创建消息成功"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        mock_db.set_table_data("conversations", [conversation])

        new_message = create_test_message(
            conversation_id=conversation["id"],
            content="测试消息"
        )
        mock_db.table("messages").execute = MagicMock(
            return_value=MagicMock(data=[new_message])
        )

        # Mock conversation_service 方法
        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            with patch.object(
                message_service.conversation_service,
                "increment_message_count",
                return_value=None
            ):
                with patch.object(
                    message_service.conversation_service,
                    "update_last_message_preview",
                    return_value=None
                ):
                    # Act
                    result = await message_service.create_message(
                        conversation_id=conversation["id"],
                        user_id=user["id"],
                        content="测试消息",
                        role="user"
                    )

        # Assert
        assert result["content"] == "测试消息"
        assert result["role"] == "user"

    @pytest.mark.asyncio
    async def test_create_message_with_image(self, message_service, mock_db):
        """测试：创建带图片的消息"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])

        new_message = create_test_message(conversation_id=conversation["id"])
        new_message["image_url"] = "https://example.com/image.jpg"
        mock_db.table("messages").execute = MagicMock(
            return_value=MagicMock(data=[new_message])
        )

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            with patch.object(
                message_service.conversation_service,
                "increment_message_count",
                return_value=None
            ):
                with patch.object(
                    message_service.conversation_service,
                    "update_last_message_preview",
                    return_value=None
                ):
                    # Act
                    result = await message_service.create_message(
                        conversation_id=conversation["id"],
                        user_id=user["id"],
                        content="图片消息",
                        image_url="https://example.com/image.jpg"
                    )

        # Assert
        assert result["image_url"] == "https://example.com/image.jpg"

    @pytest.mark.asyncio
    async def test_create_error_message(self, message_service, mock_db):
        """测试：创建错误消息"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])

        error_message = create_test_message(
            conversation_id=conversation["id"],
            role="assistant",
            content="AI 调用失败"
        )
        error_message["is_error"] = True
        mock_db.table("messages").execute = MagicMock(
            return_value=MagicMock(data=[error_message])
        )

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            with patch.object(
                message_service.conversation_service,
                "update_last_message_preview",
                return_value=None
            ):
                # Act
                result = await message_service.create_error_message(
                    conversation_id=conversation["id"],
                    user_id=user["id"],
                    content="AI 调用失败"
                )

        # Assert
        assert result["role"] == "assistant"
        assert result["is_error"] is True


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
    async def test_delete_message_permission_denied(self, message_service, mock_db):
        """测试：无权删除他人消息"""
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

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation
        ):
            # Act & Assert
            with pytest.raises(PermissionDeniedError):
                await message_service.delete_message(
                    message_id=message["id"],
                    user_id=other_user["id"]  # 非所有者
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

    @pytest.mark.asyncio
    async def test_get_conversation_history_with_attachments(self, message_service, mock_db):
        """测试：获取带附件的对话历史"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        messages = [
            {
                "id": str(uuid4()),
                "conversation_id": conversation["id"],
                "role": "user",
                "content": "这是一张图片",
                "image_url": "https://example.com/image.jpg",
                "video_url": None,
            },
        ]

        with patch.object(
            message_service,
            "get_messages",
            return_value={"messages": messages, "total": 1}
        ):
            # Act
            history = await message_service._get_conversation_history(
                conversation_id=conversation["id"],
                user_id=user["id"],
                limit=10
            )

        # Assert
        assert len(history) == 1
        assert "attachments" in history[0]
        assert history[0]["attachments"][0]["type"] == "image"
