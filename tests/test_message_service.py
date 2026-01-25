"""
消息服务单元测试

测试 MessageService 的核心功能：创建、查询、删除消息。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.message_service import MessageService
from core.exceptions import NotFoundError, PermissionDeniedError


class TestMessageServiceCreate:
    """消息创建测试"""

    @pytest.mark.asyncio
    async def test_create_message_success(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试创建消息成功"""
        service = MessageService(mock_db)

        # Mock conversation_service.get_conversation
        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            # Mock conversation_service 的其他方法
            with patch.object(
                service.conversation_service, "increment_message_count", new_callable=AsyncMock
            ), patch.object(
                service.conversation_service, "update_last_message_preview", new_callable=AsyncMock
            ):
                # Mock 数据库插入
                mock_db.table = MagicMock(return_value=MagicMock(
                    insert=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_message]))
                    ))
                ))

                result = await service.create_message(
                    conversation_id="conv-123",
                    user_id="user-123",
                    content="测试消息",
                    role="user",
                )

        assert result["id"] == mock_message["id"]
        assert result["content"] == mock_message["content"]

    @pytest.mark.asyncio
    async def test_create_message_with_image(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试创建带图片的消息"""
        service = MessageService(mock_db)
        mock_message["image_url"] = "https://example.com/image.jpg"

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            with patch.object(
                service.conversation_service, "increment_message_count", new_callable=AsyncMock
            ), patch.object(
                service.conversation_service, "update_last_message_preview", new_callable=AsyncMock
            ):
                mock_db.table = MagicMock(return_value=MagicMock(
                    insert=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_message]))
                    ))
                ))

                result = await service.create_message(
                    conversation_id="conv-123",
                    user_id="user-123",
                    content="测试消息",
                    role="user",
                    image_url="https://example.com/image.jpg",
                )

        assert result["image_url"] == "https://example.com/image.jpg"


class TestMessageServiceGet:
    """消息查询测试"""

    @pytest.mark.asyncio
    async def test_get_messages_success(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试获取消息列表成功"""
        service = MessageService(mock_db)

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            # 创建支持链式调用的 mock
            mock_query = MagicMock()
            mock_query.select.return_value = mock_query
            mock_query.eq.return_value = mock_query
            mock_query.order.return_value = mock_query
            mock_query.limit.return_value = mock_query
            mock_query.offset.return_value = mock_query
            mock_query.execute.return_value = MagicMock(data=[mock_message])
            mock_db.table = MagicMock(return_value=mock_query)

            result = await service.get_messages(
                conversation_id="conv-123",
                user_id="user-123",
                limit=50,
                offset=0,
            )

        assert "messages" in result
        assert len(result["messages"]) == 1
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_get_message_single_success(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试获取单条消息成功"""
        service = MessageService(mock_db)

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        eq=MagicMock(return_value=MagicMock(
                            single=MagicMock(return_value=MagicMock(
                                execute=MagicMock(return_value=MagicMock(data=mock_message))
                            ))
                        ))
                    ))
                ))
            ))

            result = await service.get_message(
                conversation_id="conv-123",
                message_id="msg-123",
                user_id="user-123",
            )

        assert result["id"] == mock_message["id"]

    @pytest.mark.asyncio
    async def test_get_message_not_found(
        self, mock_db: MagicMock, mock_conversation: dict
    ) -> None:
        """测试消息不存在"""
        service = MessageService(mock_db)

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        eq=MagicMock(return_value=MagicMock(
                            single=MagicMock(return_value=MagicMock(
                                execute=MagicMock(return_value=MagicMock(data=None))
                            ))
                        ))
                    ))
                ))
            ))

            with pytest.raises(NotFoundError):
                await service.get_message(
                    conversation_id="conv-123",
                    message_id="non-existent",
                    user_id="user-123",
                )


class TestMessageServiceDelete:
    """消息删除测试"""

    @pytest.mark.asyncio
    async def test_delete_message_success(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试删除消息成功"""
        service = MessageService(mock_db)

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            # Mock 查询消息
            mock_select = MagicMock()
            mock_select.select.return_value.eq.return_value.execute.return_value.data = [mock_message]

            # Mock 删除消息
            mock_delete = MagicMock()
            mock_delete.delete.return_value.eq.return_value.execute.return_value = MagicMock()

            call_count = 0

            def mock_table(name: str) -> MagicMock:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return mock_select
                return mock_delete

            mock_db.table = mock_table

            result = await service.delete_message(
                message_id="msg-123",
                user_id="user-123",
            )

        assert result["id"] == mock_message["id"]
        assert result["conversation_id"] == mock_message["conversation_id"]

    @pytest.mark.asyncio
    async def test_delete_message_not_found(self, mock_db: MagicMock) -> None:
        """测试删除不存在的消息"""
        service = MessageService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        ))

        with pytest.raises(NotFoundError):
            await service.delete_message(
                message_id="non-existent",
                user_id="user-123",
            )

    @pytest.mark.asyncio
    async def test_delete_message_permission_denied(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试无权删除消息"""
        service = MessageService(mock_db)
        mock_conversation["user_id"] = "other-user"

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_message]))
                    ))
                ))
            ))

            with pytest.raises(PermissionDeniedError):
                await service.delete_message(
                    message_id="msg-123",
                    user_id="user-123",
                )


class TestMessageServiceHelpers:
    """辅助方法测试"""

    @pytest.mark.asyncio
    async def test_get_conversation_history(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试获取对话历史"""
        service = MessageService(mock_db)

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            # 创建支持链式调用的 mock
            mock_query = MagicMock()
            mock_query.select.return_value = mock_query
            mock_query.eq.return_value = mock_query
            mock_query.order.return_value = mock_query
            mock_query.limit.return_value = mock_query
            mock_query.offset.return_value = mock_query
            mock_query.execute.return_value = MagicMock(data=[mock_message])
            mock_db.table = MagicMock(return_value=mock_query)

            result = await service._get_conversation_history(
                conversation_id="conv-123",
                user_id="user-123",
                limit=10,
            )

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == mock_message["content"]

    @pytest.mark.asyncio
    async def test_get_conversation_history_with_attachments(
        self, mock_db: MagicMock, mock_conversation: dict, mock_message: dict
    ) -> None:
        """测试获取带附件的对话历史"""
        service = MessageService(mock_db)
        mock_message["image_url"] = "https://example.com/image.jpg"

        with patch.object(
            service.conversation_service, "get_conversation", new_callable=AsyncMock
        ) as mock_get_conv:
            mock_get_conv.return_value = mock_conversation

            # 创建支持链式调用的 mock
            mock_query = MagicMock()
            mock_query.select.return_value = mock_query
            mock_query.eq.return_value = mock_query
            mock_query.order.return_value = mock_query
            mock_query.limit.return_value = mock_query
            mock_query.offset.return_value = mock_query
            mock_query.execute.return_value = MagicMock(data=[mock_message])
            mock_db.table = MagicMock(return_value=mock_query)

            result = await service._get_conversation_history(
                conversation_id="conv-123",
                user_id="user-123",
                limit=10,
            )

        assert len(result) == 1
        assert "attachments" in result[0]
        assert result[0]["attachments"][0]["type"] == "image"
