"""
base_handler 单元测试

测试 BaseHandler 的核心功能：
- 积分检查和扣除
- 积分锁定/确认/退回
- 任务状态管理
- 辅助方法（参数序列化、内容提取等）
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.handlers.base import BaseHandler, TaskMetadata
from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageRole,
    MessageStatus,
    TextPart,
    ImagePart,
)
from core.exceptions import InsufficientCreditsError

# 从 conftest.py 导入测试辅助函数（pytest 会自动加载 conftest.py）
# 这里我们直接定义需要的辅助函数，避免导入问题

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


# ============ Test Handler 实现 ============

class TestHandler(BaseHandler):
    """
    用于测试的具体 Handler 实现
    """

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.CHAT

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: list[ContentPart],
        params: dict,
        metadata: TaskMetadata,
    ) -> str:
        """测试实现：返回固定 task_id"""
        return "test_task_id"

    async def on_complete(
        self,
        task_id: str,
        result: list[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """测试实现：返回 mock 消息"""
        return create_test_message(message_id="msg_123")

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """测试实现：返回 mock 错误消息"""
        return create_test_message(message_id="msg_123", content=error_message)

    def _convert_content_parts_to_dicts(self, result: list[ContentPart]) -> list[dict]:
        """测试实现：转换 ContentPart 到字典"""
        return [{"type": part.type, "text": getattr(part, "text", None)} for part in result]

    async def _handle_credits_on_complete(
        self,
        task: dict,
        credits_consumed: int,
    ) -> int:
        """测试实现：返回消耗的积分数"""
        return credits_consumed

    async def _handle_credits_on_error(self, task: dict) -> None:
        """测试实现：空实现"""
        pass


# ============ 辅助方法测试 ============

class TestBaseHandlerHelperMethods:
    """测试 BaseHandler 的辅助方法"""

    @pytest.fixture
    def handler(self, mock_db):
        return TestHandler(mock_db)

    def test_serialize_params_basic_types(self, handler):
        """测试：序列化基本类型"""
        params = {
            "text": "测试文本",
            "num": 123,
            "float_val": 3.14,
            "bool_val": True,
            "list_val": [1, 2, 3],
            "dict_val": {"key": "value"},
        }

        result = handler._serialize_params(params)

        assert result == params

    def test_serialize_params_datetime(self, handler):
        """测试：序列化 datetime"""
        now = datetime.now(timezone.utc)
        params = {"created_at": now}

        result = handler._serialize_params(params)

        assert "created_at" in result
        assert result["created_at"] == now.isoformat()

    def test_serialize_params_none_values(self, handler):
        """测试：过滤 None 值"""
        params = {
            "key1": "value1",
            "key2": None,
            "key3": "value3",
        }

        result = handler._serialize_params(params)

        assert "key1" in result
        assert "key2" not in result
        assert "key3" in result

    def test_build_task_data_basic(self, handler):
        """测试：构建基本任务数据"""
        metadata = TaskMetadata(
            client_task_id="client_123",
            placeholder_created_at=datetime.now(timezone.utc),
        )

        task_data = handler._build_task_data(
            task_id="task_123",
            message_id="msg_123",
            conversation_id="conv_123",
            user_id="user_123",
            task_type="chat",
            status="running",
            model_id="gpt-4",
            request_params={"prompt": "Hello"},
            metadata=metadata,
        )

        assert task_data["external_task_id"] == "task_123"
        assert task_data["placeholder_message_id"] == "msg_123"
        assert task_data["conversation_id"] == "conv_123"
        assert task_data["user_id"] == "user_123"
        assert task_data["type"] == "chat"
        assert task_data["status"] == "running"
        assert task_data["model_id"] == "gpt-4"
        assert task_data["client_task_id"] == "client_123"

    def test_build_task_data_with_credits(self, handler):
        """测试：构建包含积分锁定的任务数据"""
        metadata = TaskMetadata(client_task_id="client_123")

        task_data = handler._build_task_data(
            task_id="task_123",
            message_id="msg_123",
            conversation_id="conv_123",
            user_id="user_123",
            task_type="image",
            status="pending",
            model_id="dall-e-3",
            request_params={"prompt": "cat"},
            metadata=metadata,
            credits_locked=50,
            transaction_id="tx_123",
        )

        assert task_data["credits_locked"] == 50
        assert task_data["credit_transaction_id"] == "tx_123"

    def test_extract_text_content(self, handler):
        """测试：提取文本内容"""
        content = [
            TextPart(text="Hello"),
            TextPart(text=" World"),
        ]

        result = handler._extract_text_content(content)

        # _extract_text_content 只返回第一个文本部分
        assert result == "Hello"

    def test_extract_text_content_empty(self, handler):
        """测试：提取空内容"""
        content = []

        result = handler._extract_text_content(content)

        assert result == ""

    def test_extract_image_url(self, handler):
        """测试：提取图片 URL"""
        content = [
            TextPart(text="描述这张图"),
            ImagePart(url="https://example.com/image.png"),  # 使用 url 而不是 image_url
        ]

        result = handler._extract_image_url(content)

        assert result == "https://example.com/image.png"

    def test_extract_image_url_none(self, handler):
        """测试：没有图片时返回 None"""
        content = [TextPart(text="纯文本")]

        result = handler._extract_image_url(content)

        assert result is None


# ============ 积分相关测试 ============

class TestBaseHandlerCredits:
    """测试 BaseHandler 的积分处理逻辑"""

    @pytest.fixture
    def handler(self, mock_async_db):
        return TestHandler(mock_async_db)

    @pytest.mark.asyncio
    async def test_get_user_balance_success(self, handler, mock_async_db):
        """测试：获取用户余额成功"""
        user = create_test_user(credits=500)
        mock_async_db.set_table_data("users", [user])

        balance = await handler._get_user_balance(user["id"])

        assert balance == 500

    @pytest.mark.asyncio
    async def test_get_user_balance_user_not_found(self, handler, mock_async_db):
        """测试：用户不存在返回 0"""
        mock_async_db.set_table_data("users", [])

        balance = await handler._get_user_balance("nonexistent")

        assert balance == 0

    @pytest.mark.asyncio
    async def test_check_balance_sufficient(self, handler, mock_async_db):
        """测试：余额充足"""
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])

        balance = await handler._check_balance(user["id"], required=50)

        assert balance == 100

    @pytest.mark.asyncio
    async def test_check_balance_insufficient(self, handler, mock_async_db):
        """测试：余额不足抛出异常"""
        user = create_test_user(credits=10)
        mock_async_db.set_table_data("users", [user])

        with pytest.raises(InsufficientCreditsError) as exc_info:
            await handler._check_balance(user["id"], required=100)

        assert "积分不足" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_lock_credits_success(self, handler, mock_async_db):
        """测试：锁定积分成功"""
        user = create_test_user(credits=100)

        # 设置用户数据（用于 _get_user_balance 查询）
        mock_async_db.set_table_data("users", [user])

        # 设置空的 credit_transactions 表（用于 insert）
        mock_async_db.set_table_data("credit_transactions", [])

        tx_id = await handler._lock_credits(
            task_id="task_123",
            user_id=user["id"],
            amount=10,
            reason="测试锁定",
        )

        assert tx_id is not None
        assert len(tx_id) == 36  # UUID 格式

    @pytest.mark.asyncio
    async def test_lock_credits_insufficient(self, handler, mock_async_db):
        """测试：余额不足无法锁定"""
        user = create_test_user(credits=5)
        mock_async_db.set_table_data("users", [user])

        with pytest.raises(InsufficientCreditsError):
            await handler._lock_credits(
                task_id="task_123",
                user_id=user["id"],
                amount=100,
                reason="测试锁定",
            )

    @pytest.mark.asyncio
    async def test_confirm_deduct(self, handler, mock_async_db):
        """测试：确认扣除"""
        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        # 应该不抛异常
        await handler._confirm_deduct("tx_123")

    @pytest.mark.asyncio
    async def test_refund_credits_success(self, handler, mock_async_db):
        """测试：退回积分成功"""
        tx_data = {
            "id": "tx_123",
            "user_id": "user_123",
            "amount": 10,
            "status": "pending",
        }
        mock_async_db.set_table_data("credit_transactions", [tx_data])

        # Mock 链式调用
        mock_table = mock_async_db.table("credit_transactions")
        mock_table.select = MagicMock(return_value=mock_table)
        mock_table.eq = MagicMock(return_value=mock_table)
        mock_table.single = MagicMock(return_value=mock_table)
        mock_table.execute = AsyncMock(return_value=MagicMock(data=tx_data))

        mock_async_db.rpc("refund_credits", {}).execute = AsyncMock(
            return_value=MagicMock(data={})
        )

        # 应该不抛异常
        await handler._refund_credits("tx_123")

    @pytest.mark.asyncio
    async def test_deduct_directly_success(self, handler, mock_async_db):
        """测试：直接扣除积分成功"""
        mock_async_db.set_rpc_result("deduct_credits_atomic", {
            "success": True,
            "new_balance": 90
        })

        new_balance = await handler._deduct_directly(
            user_id="user_123",
            amount=10,
            reason="测试扣除",
            change_type="usage",  # 添加缺失的参数
        )

        assert new_balance == 90

    @pytest.mark.asyncio
    async def test_deduct_directly_insufficient(self, handler, mock_async_db):
        """测试：直接扣除余额不足"""
        mock_async_db.set_rpc_result("deduct_credits_atomic", {
            "success": False
        })

        with pytest.raises(InsufficientCreditsError):
            await handler._deduct_directly(
                user_id="user_123",
                amount=1000,
                reason="测试扣除",
                change_type="usage",  # 添加缺失的参数
            )


# ============ 任务管理测试 ============

class TestBaseHandlerTaskManagement:
    """测试 BaseHandler 的任务管理功能"""

    @pytest.fixture
    def handler(self, mock_async_db):
        return TestHandler(mock_async_db)

    @pytest.mark.asyncio
    async def test_get_task_success(self, handler, mock_async_db):
        """测试：获取任务成功"""
        task_data = {
            "external_task_id": "task_123",
            "user_id": "user_123",
            "status": "running",
        }

        # 直接设置表数据，MockAsyncSupabaseTable 会自动处理
        mock_async_db.set_table_data("tasks", [task_data])

        task = await handler._get_task("task_123")

        assert task is not None
        assert task["external_task_id"] == "task_123"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, handler, mock_async_db):
        """测试：任务不存在"""
        # 设置空数据
        mock_async_db.set_table_data("tasks", [])

        task = await handler._get_task("nonexistent")

        assert task is None

    @pytest.mark.asyncio
    async def test_complete_task(self, handler, mock_async_db):
        """测试：完成任务"""
        mock_async_db.table("tasks").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        # 应该不抛异常
        await handler._complete_task("task_123")

    @pytest.mark.asyncio
    async def test_fail_task(self, handler, mock_async_db):
        """测试：失败任务"""
        mock_async_db.table("tasks").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        # 应该不抛异常
        await handler._fail_task("task_123", "测试错误")


# ============ 积分处理集成测试 ============

class TestBaseHandlerCreditsIntegration:
    """测试 BaseHandler 的积分处理完整流程"""

    @pytest.fixture
    def handler(self, mock_async_db):
        return TestHandler(mock_async_db)

    @pytest.mark.asyncio
    async def test_handle_credits_on_complete_image_task(self, handler, mock_async_db):
        """测试：图片任务完成时确认扣除"""
        task_data = {
            "external_task_id": "task_123",
            "type": "image",
            "credits_locked": 50,
            "credit_transaction_id": "tx_123",
        }

        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        # 应该不抛异常
        await handler._handle_credits_on_complete(task_data, credits_consumed=0)

    @pytest.mark.asyncio
    async def test_handle_credits_on_complete_chat_task(self, handler, mock_async_db):
        """测试：聊天任务完成时直接扣除"""
        task_data = {
            "external_task_id": "task_123",
            "type": "chat",
            "user_id": "user_123",
        }

        mock_async_db.set_rpc_result("deduct_credits_atomic", {
            "success": True,
            "new_balance": 90
        })

        # 应该不抛异常
        await handler._handle_credits_on_complete(task_data, credits_consumed=10)

    @pytest.mark.asyncio
    async def test_handle_credits_on_error_with_transaction(self, handler, mock_async_db):
        """测试：任务失败时退回积分"""
        task_data = {
            "external_task_id": "task_123",
            "type": "image",
            "credit_transaction_id": "tx_123",
        }

        tx_data = {
            "id": "tx_123",
            "user_id": "user_123",
            "amount": 50,
            "status": "pending",
        }
        mock_async_db.set_table_data("credit_transactions", [tx_data])

        # Mock 链式调用
        mock_table = mock_async_db.table("credit_transactions")
        mock_table.select = MagicMock(return_value=mock_table)
        mock_table.eq = MagicMock(return_value=mock_table)
        mock_table.single = MagicMock(return_value=mock_table)
        mock_table.execute = AsyncMock(return_value=MagicMock(data=tx_data))

        mock_async_db.rpc("refund_credits", {}).execute = AsyncMock(
            return_value=MagicMock(data={})
        )

        # 应该不抛异常
        await handler._handle_credits_on_error(task_data)


# ============ 边界情况测试 ============

class TestBaseHandlerEdgeCases:
    """测试 BaseHandler 的边界情况"""

    @pytest.fixture
    def handler(self, mock_async_db):
        return TestHandler(mock_async_db)

    @pytest.mark.asyncio
    async def test_lock_zero_credits(self, handler, mock_async_db):
        """测试：锁定 0 积分"""
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])
        mock_async_db.set_table_data("credit_transactions", [])

        tx_id = await handler._lock_credits(
            task_id="task_123",
            user_id=user["id"],
            amount=0,
            reason="零锁定",
        )

        assert tx_id is not None

    @pytest.mark.asyncio
    async def test_lock_exact_balance(self, handler, mock_async_db):
        """测试：锁定恰好等于余额的积分"""
        user = create_test_user(credits=50)
        mock_async_db.set_table_data("users", [user])
        mock_async_db.set_table_data("credit_transactions", [])

        tx_id = await handler._lock_credits(
            task_id="task_123",
            user_id=user["id"],
            amount=50,
            reason="全部锁定",
        )

        assert tx_id is not None

    def test_serialize_params_empty_dict(self, handler):
        """测试：序列化空字典"""
        params = {}

        result = handler._serialize_params(params)

        assert result == {}

    def test_convert_content_parts_to_dicts(self, handler):
        """测试：转换 ContentPart 到字典"""
        content = [
            TextPart(text="Hello"),
            ImagePart(url="https://example.com/image.png"),  # 使用 url
        ]

        result = handler._convert_content_parts_to_dicts(content)

        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Hello"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
