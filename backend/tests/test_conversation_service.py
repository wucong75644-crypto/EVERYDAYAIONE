"""
conversation_service 单元测试

测试对话服务的核心功能和异常处理：
- 创建对话
- 获取对话
- 获取对话列表
- 更新对话
- 删除对话
- 异常处理
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

from services.conversation_service import ConversationService
from core.exceptions import NotFoundError, PermissionDeniedError, AppException


def create_test_user(
    user_id: str = None,
    phone: str = "13800138000",
    nickname: str = "测试用户",
    credits: int = 100,
) -> dict:
    """创建测试用户数据"""
    from datetime import datetime, timezone
    return {
        "id": user_id or str(uuid4()),
        "phone": phone,
        "nickname": nickname,
        "credits": credits,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def create_test_conversation(
    conversation_id: str = None,
    user_id: str = None,
    title: str = "测试对话",
    org_id: str = None,
) -> dict:
    """创建测试对话数据"""
    from datetime import datetime, timezone
    return {
        "id": conversation_id or str(uuid4()),
        "user_id": user_id or str(uuid4()),
        "title": title,
        "model_id": "gpt-4",
        "message_count": 0,
        "credits_consumed": 0,
        "org_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class TestConversationServiceCreate:
    """对话创建测试"""

    @pytest.fixture
    def conversation_service(self, mock_db):
        return ConversationService(mock_db)

    @pytest.mark.asyncio
    async def test_create_conversation_success(self, conversation_service, mock_db):
        """测试：创建对话成功"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"], title="新对话")

        mock_query = MagicMock()
        mock_query.insert.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[conversation])
        mock_db.table = MagicMock(return_value=mock_query)

        # Act
        result = await conversation_service.create_conversation(
            user_id=user["id"],
            title="新对话"
        )

        # Assert
        assert result["id"] == conversation["id"]
        assert result["title"] == "新对话"

    @pytest.mark.asyncio
    async def test_create_conversation_db_error(self, conversation_service, mock_db):
        """测试：数据库错误时抛出 AppException"""
        # Arrange
        user = create_test_user()

        mock_query = MagicMock()
        mock_query.insert.return_value = mock_query
        mock_query.execute.side_effect = Exception("Database connection error")
        mock_db.table = MagicMock(return_value=mock_query)

        # Act & Assert
        with pytest.raises(AppException) as exc_info:
            await conversation_service.create_conversation(
                user_id=user["id"],
                title="新对话"
            )
        assert exc_info.value.code == "CONVERSATION_CREATE_ERROR"


class TestConversationServiceGet:
    """对话查询测试"""

    @pytest.fixture
    def conversation_service(self, mock_db):
        return ConversationService(mock_db)

    @pytest.mark.asyncio
    async def test_get_conversation_success(self, conversation_service, mock_db):
        """测试：获取对话成功"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[conversation])
        mock_db.table = MagicMock(return_value=mock_query)

        # Act
        result = await conversation_service.get_conversation(
            conversation_id=conversation["id"],
            user_id=user["id"]
        )

        # Assert
        assert result["id"] == conversation["id"]

    @pytest.mark.asyncio
    async def test_get_conversation_not_found(self, conversation_service, mock_db):
        """测试：对话不存在时抛出 NotFoundError"""
        # Arrange
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[])
        mock_db.table = MagicMock(return_value=mock_query)

        # Act & Assert
        with pytest.raises(NotFoundError):
            await conversation_service.get_conversation(
                conversation_id="nonexistent",
                user_id="user_123"
            )

    @pytest.mark.asyncio
    async def test_get_conversation_other_user_not_found(self, conversation_service, mock_db):
        """测试：其他用户访问时返回 NotFoundError（SQL 层过滤，不暴露存在性）"""
        # Arrange: 使用 MockSupabaseClient，user_id 不匹配时查询结果为空
        owner = create_test_user(user_id="owner_123")
        other_user = create_test_user(user_id="other_123")
        conversation = create_test_conversation(user_id=owner["id"])

        mock_db.set_table_data("conversations", [conversation])

        # Act & Assert
        with pytest.raises(NotFoundError):
            await conversation_service.get_conversation(
                conversation_id=conversation["id"],
                user_id=other_user["id"]
            )

    @pytest.mark.asyncio
    async def test_get_conversation_db_error(self, conversation_service, mock_db):
        """测试：数据库错误时抛出 AppException"""
        # Arrange
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.execute.side_effect = Exception("Database error")
        mock_db.table = MagicMock(return_value=mock_query)

        # Act & Assert
        with pytest.raises(AppException) as exc_info:
            await conversation_service.get_conversation(
                conversation_id="conv_123",
                user_id="user_123"
            )
        assert exc_info.value.code == "CONVERSATION_GET_ERROR"


class TestFormatConversation:
    """_format_conversation 格式化测试"""

    @pytest.fixture
    def conversation_service(self, mock_db):
        return ConversationService(mock_db)

    def test_includes_context_summary(self, conversation_service):
        """context_summary 字段正确传递"""
        conversation = create_test_conversation()
        conversation["context_summary"] = "用户讨论了Python编程"

        result = conversation_service._format_conversation(conversation)

        assert result["context_summary"] == "用户讨论了Python编程"

    def test_context_summary_defaults_to_none(self, conversation_service):
        """无 context_summary 字段时默认 None"""
        conversation = create_test_conversation()

        result = conversation_service._format_conversation(conversation)

        assert result["context_summary"] is None


class TestConversationServiceUpdate:
    """对话更新测试"""

    @pytest.fixture
    def conversation_service(self, mock_db):
        return ConversationService(mock_db)

    @pytest.mark.asyncio
    async def test_update_conversation_success(self, conversation_service, mock_db):
        """测试：更新对话成功"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"], title="旧标题")
        updated_conversation = {**conversation, "title": "新标题"}

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.update.return_value = mock_query
        mock_query.execute.side_effect = [
            MagicMock(data=[conversation]),  # get_conversation 调用
            MagicMock(data=[updated_conversation]),  # update 调用
        ]
        mock_db.table = MagicMock(return_value=mock_query)

        # Act
        result = await conversation_service.update_conversation(
            conversation_id=conversation["id"],
            user_id=user["id"],
            title="新标题"
        )

        # Assert
        assert result["title"] == "新标题"


class TestConversationServiceDelete:
    """对话删除测试"""

    @pytest.fixture
    def conversation_service(self, mock_db):
        return ConversationService(mock_db)

    @pytest.mark.asyncio
    async def test_delete_conversation_success(self, conversation_service, mock_db):
        """测试：删除对话成功"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.delete.return_value = mock_query
        mock_query.execute.side_effect = [
            MagicMock(data=[conversation]),  # get_conversation 调用
            MagicMock(data=[]),  # delete 调用
        ]
        mock_db.table = MagicMock(return_value=mock_query)

        # Act
        result = await conversation_service.delete_conversation(
            conversation_id=conversation["id"],
            user_id=user["id"]
        )

        # Assert
        assert result is True


# ── 企业模式（org_id）隔离测试 ─────────────────────────────


class TestConversationServiceOrgIsolation:
    """企业模式 org_id 隔离测试（使用 MockSupabaseClient 真实过滤）"""

    @pytest.fixture
    def svc(self, mock_db):
        return ConversationService(mock_db)

    @pytest.mark.asyncio
    async def test_create_with_org_id(self, svc, mock_db):
        """企业模式创建对话带 org_id"""
        org_id = "org-001"
        user_id = "user-001"
        conv = create_test_conversation(user_id=user_id, org_id=org_id, title="企业对话")
        mock_db.set_table_data("conversations", [conv])

        # MagicMock for insert (insert 不走 _apply_filters)
        mock_query = MagicMock()
        mock_query.insert.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[conv])
        mock_db.table = MagicMock(return_value=mock_query)

        result = await svc.create_conversation(
            user_id=user_id, title="企业对话", org_id=org_id,
        )
        assert result["title"] == "企业对话"
        # 验证 insert 被调用时包含 org_id
        insert_data = mock_query.insert.call_args[0][0]
        assert insert_data["org_id"] == org_id

    @pytest.mark.asyncio
    async def test_get_org_conversation_success(self, svc, mock_db):
        """企业成员能查到自己企业的对话"""
        org_id = "org-001"
        user_id = "user-001"
        conv = create_test_conversation(user_id=user_id, org_id=org_id)
        mock_db.set_table_data("conversations", [conv])

        result = await svc.get_conversation(conv["id"], user_id, org_id=org_id)
        assert result["id"] == conv["id"]

    @pytest.mark.asyncio
    async def test_get_org_conversation_wrong_org(self, svc, mock_db):
        """不同企业看不到对方的对话"""
        conv = create_test_conversation(user_id="user-001", org_id="org-001")
        mock_db.set_table_data("conversations", [conv])

        with pytest.raises(NotFoundError):
            await svc.get_conversation(conv["id"], "user-001", org_id="org-999")

    @pytest.mark.asyncio
    async def test_get_org_conversation_personal_cant_see_org(self, svc, mock_db):
        """散客看不到企业对话（org_id IS NULL 过滤不到 org_id=org-001 的数据）"""
        conv = create_test_conversation(user_id="user-001", org_id="org-001")
        mock_db.set_table_data("conversations", [conv])

        with pytest.raises(NotFoundError):
            await svc.get_conversation(conv["id"], "user-001", org_id=None)

    @pytest.mark.asyncio
    async def test_get_org_conversation_org_cant_see_personal(self, svc, mock_db):
        """企业模式看不到散客对话（org_id=org-001 过滤不到 org_id=None 的数据）"""
        conv = create_test_conversation(user_id="user-001", org_id=None)
        mock_db.set_table_data("conversations", [conv])

        with pytest.raises(NotFoundError):
            await svc.get_conversation(conv["id"], "user-001", org_id="org-001")

    @pytest.mark.asyncio
    async def test_list_org_conversations_isolated(self, svc, mock_db):
        """对话列表按 org_id 隔离"""
        user_id = "user-001"
        conv_personal = create_test_conversation(user_id=user_id, org_id=None, title="散客对话")
        conv_org1 = create_test_conversation(user_id=user_id, org_id="org-001", title="企业1对话")
        conv_org2 = create_test_conversation(user_id=user_id, org_id="org-002", title="企业2对话")
        mock_db.set_table_data("conversations", [conv_personal, conv_org1, conv_org2])

        # 散客模式：只看到散客对话
        result = await svc.get_conversation_list(user_id, org_id=None)
        assert len(result["conversations"]) == 1
        assert result["conversations"][0]["title"] == "散客对话"

        # 企业1模式：只看到企业1对话
        mock_db.set_table_data("conversations", [conv_personal, conv_org1, conv_org2])
        result = await svc.get_conversation_list(user_id, org_id="org-001")
        assert len(result["conversations"]) == 1
        assert result["conversations"][0]["title"] == "企业1对话"

    @pytest.mark.asyncio
    async def test_delete_org_conversation_wrong_org_rejected(self, svc, mock_db):
        """不能删除其他企业的对话"""
        conv = create_test_conversation(user_id="user-001", org_id="org-001")
        mock_db.set_table_data("conversations", [conv])

        with pytest.raises(NotFoundError):
            await svc.delete_conversation(conv["id"], "user-001", org_id="org-999")
