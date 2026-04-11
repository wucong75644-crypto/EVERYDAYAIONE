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
        """测试：获取消息列表（首页 offset=0）"""
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        messages = [
            create_test_message(conversation_id=conversation["id"], content="消息1"),
            create_test_message(conversation_id=conversation["id"], content="消息2"),
        ]
        mock_db.set_table_data("messages", messages)

        # Mock 链式调用 — PostgREST QueryBuilder 没有 .offset 方法，
        # 必须用 .range(start, end)。spec 限制 mock attr 防止意外通过。
        mock_query = MagicMock(spec=["select", "eq", "order", "range", "execute"])
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
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
        # 验证使用 .range(0, 49) 而非 .offset() — 防止 .offset bug 回归
        mock_query.range.assert_called_once_with(0, 49)

    @pytest.mark.asyncio
    async def test_get_messages_pagination_uses_range_not_offset(
        self, message_service, mock_db
    ):
        """回归测试：翻页（offset>0）必须用 .range(start, end) 而非 .offset()

        历史 bug：PostgREST QueryBuilder 没有 .offset() 方法，
        旧代码 query.offset(offset) 在生产 offset>0 时全部 500。
        本测试用 spec 严格 mock，确保未来不会再次调到不存在的 .offset。
        """
        # Arrange
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        messages = [
            create_test_message(conversation_id=conversation["id"], content=f"消息{i}")
            for i in range(30)
        ]

        # spec 严格限制 mock 可调用的方法：没有 offset
        # 如果生产代码再次调 .offset()，spec mock 会抛 AttributeError
        mock_query = MagicMock(spec=["select", "eq", "order", "range", "execute"])
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=messages)
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation,
        ):
            # Act：第二页（offset=30 limit=30）
            result = await message_service.get_messages(
                conversation_id=conversation["id"],
                user_id=user["id"],
                limit=30,
                offset=30,
            )

        # Assert
        assert result["total"] == 30
        # range 是闭区间，offset=30 limit=30 → range(30, 59)
        mock_query.range.assert_called_once_with(30, 59)
        # 严格断言 .offset 从未被调用（spec 已经会抛 AttributeError，这里双保险）
        assert not hasattr(mock_query, "offset") or not mock_query.offset.called

    @pytest.mark.asyncio
    async def test_get_messages_offset_zero_starts_from_beginning(
        self, message_service, mock_db
    ):
        """边界测试：offset=0 时 range 从 0 开始"""
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        messages = [create_test_message(conversation_id=conversation["id"])]

        mock_query = MagicMock(spec=["select", "eq", "order", "range", "execute"])
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=messages)
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation,
        ):
            await message_service.get_messages(
                conversation_id=conversation["id"],
                user_id=user["id"],
                limit=10,
                offset=0,
            )

        mock_query.range.assert_called_once_with(0, 9)

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


class TestMessageServiceSearch:
    """消息搜索测试（Phase 1：cursor 分页 + 搜索方案）"""

    @pytest.fixture
    def message_service(self, mock_db):
        return MessageService(mock_db)

    @pytest.mark.asyncio
    async def test_search_messages_returns_matches(self, message_service, mock_db):
        """搜索关键词返回匹配的消息"""
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])
        matching_msgs = [
            create_test_message(conversation_id=conversation["id"], content="测试消息一"),
            create_test_message(conversation_id=conversation["id"], content="测试消息二"),
        ]

        # spec 严格 mock：必须用 ilike + range（不能用 .offset()）
        mock_query = MagicMock(
            spec=["select", "eq", "ilike", "order", "range", "execute"]
        )
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.ilike.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=matching_msgs)
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation,
        ):
            result = await message_service.search_messages(
                conversation_id=conversation["id"],
                user_id=user["id"],
                query="测试",
                limit=20,
            )

        assert result["total"] == 2
        assert result["query"] == "测试"
        # 验证使用 ILIKE 而非全表扫描
        mock_query.ilike.assert_called_once_with("content::text", "%测试%")
        # 验证使用 range（local QueryBuilder 没有 .offset 方法）
        mock_query.range.assert_called_once_with(0, 19)

    @pytest.mark.asyncio
    async def test_search_messages_empty_query_returns_empty(
        self, message_service, mock_db
    ):
        """空字符串 query 直接返回空结果，不查数据库"""
        result = await message_service.search_messages(
            conversation_id="any",
            user_id="any",
            query="",
        )
        assert result["messages"] == []
        assert result["total"] == 0
        assert result["query"] == ""

    @pytest.mark.asyncio
    async def test_search_messages_whitespace_only_query_returns_empty(
        self, message_service, mock_db
    ):
        """纯空白 query 也直接返回空"""
        result = await message_service.search_messages(
            conversation_id="any",
            user_id="any",
            query="   \t\n  ",
        )
        assert result["messages"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_search_messages_escapes_like_wildcards(
        self, message_service, mock_db
    ):
        """ILIKE 通配符 % 和 _ 在用户输入中被转义，避免误匹配"""
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])

        mock_query = MagicMock(
            spec=["select", "eq", "ilike", "order", "range", "execute"]
        )
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.ilike.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[])
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation,
        ):
            await message_service.search_messages(
                conversation_id=conversation["id"],
                user_id=user["id"],
                query="50%_test",
            )

        # % 和 _ 被转义为 \% 和 \_
        mock_query.ilike.assert_called_once_with("content::text", "%50\\%\\_test%")

    @pytest.mark.asyncio
    async def test_search_messages_caps_limit_at_100(
        self, message_service, mock_db
    ):
        """limit 上限 100，超出会被截断"""
        user = create_test_user()
        conversation = create_test_conversation(user_id=user["id"])

        mock_query = MagicMock(
            spec=["select", "eq", "ilike", "order", "range", "execute"]
        )
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.ilike.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[])
        mock_db.table = MagicMock(return_value=mock_query)

        with patch.object(
            message_service.conversation_service,
            "get_conversation",
            return_value=conversation,
        ):
            await message_service.search_messages(
                conversation_id=conversation["id"],
                user_id=user["id"],
                query="x",
                limit=999,  # 超出
            )

        # 实际 range 应该是 (0, 99)
        mock_query.range.assert_called_once_with(0, 99)


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


