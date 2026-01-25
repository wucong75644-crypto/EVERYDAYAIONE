"""
对话服务单元测试

测试 ConversationService 的核心功能：创建、查询、更新、删除对话。
"""

from unittest.mock import MagicMock

import pytest

from services.conversation_service import ConversationService
from core.exceptions import NotFoundError, PermissionDeniedError


class TestConversationServiceCreate:
    """对话创建测试"""

    @pytest.mark.asyncio
    async def test_create_conversation_success(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试创建对话成功"""
        service = ConversationService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            insert=MagicMock(return_value=MagicMock(
                execute=MagicMock(return_value=MagicMock(data=[mock_conversation]))
            ))
        ))

        result = await service.create_conversation(
            user_id="user-123",
            title="测试对话",
            model_id="gemini-3-flash",
        )

        assert result["id"] == mock_conversation["id"]
        assert result["title"] == "测试对话"
        assert result["user_id"] == "user-123"

    @pytest.mark.asyncio
    async def test_create_conversation_default_title(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试创建对话使用默认标题"""
        service = ConversationService(mock_db)
        mock_conversation["title"] = "新对话"

        mock_db.table = MagicMock(return_value=MagicMock(
            insert=MagicMock(return_value=MagicMock(
                execute=MagicMock(return_value=MagicMock(data=[mock_conversation]))
            ))
        ))

        result = await service.create_conversation(user_id="user-123")

        assert result["title"] == "新对话"


class TestConversationServiceGet:
    """对话查询测试"""

    @pytest.mark.asyncio
    async def test_get_conversation_success(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试获取对话成功"""
        service = ConversationService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[mock_conversation]))
                ))
            ))
        ))

        result = await service.get_conversation("conv-123", "user-123")

        assert result["id"] == "conv-123"
        assert result["user_id"] == "user-123"

    @pytest.mark.asyncio
    async def test_get_conversation_not_found(self, mock_db: MagicMock) -> None:
        """测试对话不存在"""
        service = ConversationService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        ))

        with pytest.raises(NotFoundError):
            await service.get_conversation("non-existent", "user-123")

    @pytest.mark.asyncio
    async def test_get_conversation_permission_denied(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试无权访问对话"""
        service = ConversationService(mock_db)
        mock_conversation["user_id"] = "other-user"

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[mock_conversation]))
                ))
            ))
        ))

        with pytest.raises(PermissionDeniedError):
            await service.get_conversation("conv-123", "user-123")


class TestConversationServiceList:
    """对话列表测试"""

    @pytest.mark.asyncio
    async def test_get_conversation_list_success(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试获取对话列表成功"""
        service = ConversationService(mock_db)

        mock_result = MagicMock()
        mock_result.data = [mock_conversation]
        mock_result.count = 1

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    order=MagicMock(return_value=MagicMock(
                        range=MagicMock(return_value=MagicMock(
                            execute=MagicMock(return_value=mock_result)
                        ))
                    ))
                ))
            ))
        ))

        result = await service.get_conversation_list("user-123", limit=10, offset=0)

        assert "conversations" in result
        assert "total" in result
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_get_conversation_list_empty(self, mock_db: MagicMock) -> None:
        """测试空对话列表"""
        service = ConversationService(mock_db)

        mock_result = MagicMock()
        mock_result.data = []
        mock_result.count = 0

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    order=MagicMock(return_value=MagicMock(
                        range=MagicMock(return_value=MagicMock(
                            execute=MagicMock(return_value=mock_result)
                        ))
                    ))
                ))
            ))
        ))

        result = await service.get_conversation_list("user-123")

        assert result["conversations"] == []
        assert result["total"] == 0


class TestConversationServiceUpdate:
    """对话更新测试"""

    @pytest.mark.asyncio
    async def test_update_conversation_title(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试更新对话标题"""
        service = ConversationService(mock_db)

        # Mock get_conversation（权限验证）
        mock_select = MagicMock()
        mock_select.select.return_value.eq.return_value.execute.return_value.data = [mock_conversation]

        # Mock update
        updated_conversation = {**mock_conversation, "title": "新标题"}
        mock_update = MagicMock()
        mock_update.update.return_value.eq.return_value.execute.return_value.data = [updated_conversation]

        call_count = 0

        def mock_table(name: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_select
            return mock_update

        mock_db.table = mock_table

        result = await service.update_conversation(
            conversation_id="conv-123",
            user_id="user-123",
            title="新标题",
        )

        assert result["title"] == "新标题"


class TestConversationServiceDelete:
    """对话删除测试"""

    @pytest.mark.asyncio
    async def test_delete_conversation_success(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试删除对话成功"""
        service = ConversationService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[mock_conversation]))
                ))
            )),
            delete=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        ))

        result = await service.delete_conversation("conv-123", "user-123")

        assert result is True


class TestConversationServiceHelpers:
    """辅助方法测试"""

    @pytest.mark.asyncio
    async def test_increment_message_count(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试增加消息计数"""
        service = ConversationService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[mock_conversation]))
                ))
            )),
            update=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        ))

        # 应该不抛出异常
        await service.increment_message_count("conv-123", credits_cost=10)

    @pytest.mark.asyncio
    async def test_update_last_message_preview(self, mock_db: MagicMock) -> None:
        """测试更新最后消息预览"""
        service = ConversationService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            update=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        ))

        # 应该不抛出异常
        await service.update_last_message_preview("conv-123", "测试消息内容")

    @pytest.mark.asyncio
    async def test_update_last_message_preview_truncate(self, mock_db: MagicMock) -> None:
        """测试更新最后消息预览（截断长内容）"""
        service = ConversationService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            update=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        ))

        long_content = "这是一段很长的消息内容" * 10
        await service.update_last_message_preview("conv-123", long_content)

    def test_format_conversation(self, mock_db: MagicMock, mock_conversation: dict) -> None:
        """测试对话响应格式化"""
        service = ConversationService(mock_db)
        result = service._format_conversation(mock_conversation)

        assert result["id"] == mock_conversation["id"]
        assert result["user_id"] == mock_conversation["user_id"]
        assert result["title"] == mock_conversation["title"]
        assert result["model_id"] == mock_conversation["model_id"]
        assert result["message_count"] == mock_conversation["message_count"]
        assert result["credits_consumed"] == mock_conversation["credits_consumed"]
