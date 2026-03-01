"""
媒体占位符入库的单元测试

验证 _handle_regenerate_or_send_operation() 对 media 类型（image/video）
将占位符消息 insert 到 messages 表的行为。

测试覆盖：
1. Image 类型 → 占位符入库
2. Video 类型 → 占位符入库
3. Chat 类型 → 不入库（保持虚拟）
4. 占位符入库失败 → 降级继续（不阻断）
5. Retry 操作 → 不经过新逻辑
6. Upsert 覆盖 → 占位符入库后 on_complete 的 upsert 能正确覆盖
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call
from datetime import datetime, timezone
from uuid import uuid4
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from schemas.message import (
    GenerationType,
    MessageRole,
    MessageStatus,
    MessageOperation,
)


# ============ Mock 工厂 ============

def _make_db_mock():
    """创建 Supabase DB mock"""
    db = MagicMock()

    # 链式调用 mock：db.table("messages").insert(data).execute()
    insert_mock = MagicMock()
    insert_mock.execute.return_value = MagicMock(data=[{"id": "test"}])

    table_mock = MagicMock()
    table_mock.insert.return_value = insert_mock
    table_mock.select.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.single.return_value = table_mock
    table_mock.execute.return_value = MagicMock(data=None)

    db.table.return_value = table_mock
    return db, table_mock, insert_mock


# ============ 测试类 ============

class TestMediaPlaceholderInsert:
    """测试 media 占位符入库"""

    @pytest.mark.asyncio
    async def test_image_placeholder_inserted_to_db(self):
        """Image 类型应将占位符 insert 到 messages 表"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db, table_mock, insert_mock = _make_db_mock()
        msg_id = str(uuid4())
        conv_id = str(uuid4())
        created_at = datetime.now(timezone.utc)

        result_id, result_msg = await _handle_regenerate_or_send_operation(
            db=db,
            conversation_id=conv_id,
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id=msg_id,
            placeholder_created_at=created_at,
            gen_type=GenerationType.IMAGE,
        )

        # 验证 insert 被调用
        db.table.assert_any_call("messages")
        insert_call_args = table_mock.insert.call_args
        assert insert_call_args is not None, "messages.insert() should be called for image type"

        # 验证 insert 的数据
        inserted_data = insert_call_args[0][0]
        assert inserted_data["id"] == msg_id
        assert inserted_data["conversation_id"] == conv_id
        assert inserted_data["role"] == "assistant"
        assert inserted_data["status"] == "pending"
        assert inserted_data["content"] == [{"type": "text", "text": "图片生成中"}]
        assert inserted_data["generation_params"] == {"type": "image"}
        assert inserted_data["credits_cost"] == 0
        assert "created_at" in inserted_data

        # 验证返回值
        assert result_id == msg_id
        assert result_msg.id == msg_id
        assert result_msg.status == MessageStatus.PENDING

    @pytest.mark.asyncio
    async def test_video_placeholder_inserted_to_db(self):
        """Video 类型应将占位符 insert 到 messages 表"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db, table_mock, insert_mock = _make_db_mock()
        msg_id = str(uuid4())
        conv_id = str(uuid4())

        result_id, result_msg = await _handle_regenerate_or_send_operation(
            db=db,
            conversation_id=conv_id,
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id=msg_id,
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.VIDEO,
        )

        # 验证 insert 数据包含视频占位符文字
        insert_call_args = table_mock.insert.call_args
        assert insert_call_args is not None
        inserted_data = insert_call_args[0][0]
        assert inserted_data["content"] == [{"type": "text", "text": "视频生成中"}]
        assert inserted_data["generation_params"] == {"type": "video"}

    @pytest.mark.asyncio
    async def test_chat_type_not_inserted_to_db(self):
        """Chat 类型不应将占位符 insert 到 messages 表"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db, table_mock, insert_mock = _make_db_mock()

        result_id, result_msg = await _handle_regenerate_or_send_operation(
            db=db,
            conversation_id=str(uuid4()),
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id=str(uuid4()),
            placeholder_created_at=None,
            gen_type=GenerationType.CHAT,
        )

        # 验证 insert 未被调用（Chat 不入库）
        table_mock.insert.assert_not_called()

        # 但仍然返回有效的虚拟 Message
        assert result_msg.status == MessageStatus.PENDING
        assert result_msg.role == MessageRole.ASSISTANT

    @pytest.mark.asyncio
    async def test_placeholder_without_created_at(self):
        """没有 placeholder_created_at 时不设置 created_at 字段（让 DB 自动生成）"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db, table_mock, insert_mock = _make_db_mock()

        await _handle_regenerate_or_send_operation(
            db=db,
            conversation_id=str(uuid4()),
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id=str(uuid4()),
            placeholder_created_at=None,  # 不传 created_at
            gen_type=GenerationType.IMAGE,
        )

        inserted_data = table_mock.insert.call_args[0][0]
        assert "created_at" not in inserted_data


class TestPlaceholderInsertFailure:
    """测试占位符入库失败时的降级行为"""

    @pytest.mark.asyncio
    async def test_insert_failure_does_not_block_task(self):
        """DB insert 失败时应降级继续，不阻断任务"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db = MagicMock()
        table_mock = MagicMock()
        db.table.return_value = table_mock

        # 让 insert().execute() 抛异常
        insert_chain = MagicMock()
        insert_chain.execute.side_effect = Exception("DB connection lost")
        table_mock.insert.return_value = insert_chain

        msg_id = str(uuid4())

        # 不应抛异常
        result_id, result_msg = await _handle_regenerate_or_send_operation(
            db=db,
            conversation_id=str(uuid4()),
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id=msg_id,
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.IMAGE,
        )

        # 仍然返回有效结果
        assert result_id == msg_id
        assert result_msg.status == MessageStatus.PENDING

    @pytest.mark.asyncio
    async def test_insert_failure_logs_warning(self):
        """DB insert 失败时应记录 warning 日志"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db = MagicMock()
        table_mock = MagicMock()
        db.table.return_value = table_mock
        insert_chain = MagicMock()
        insert_chain.execute.side_effect = Exception("timeout")
        table_mock.insert.return_value = insert_chain

        with patch("api.routes.message_generation_helpers.logger") as mock_logger:
            await _handle_regenerate_or_send_operation(
                db=db,
                conversation_id=str(uuid4()),
                operation=MessageOperation.SEND,
                original_message_id=None,
                assistant_message_id=str(uuid4()),
                placeholder_created_at=datetime.now(timezone.utc),
                gen_type=GenerationType.IMAGE,
            )

            # 验证 warning 被记录
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "Failed to save media placeholder" in warning_msg


class TestUpsertCompatibility:
    """测试占位符入库后 upsert 覆盖的兼容性"""

    @pytest.mark.asyncio
    async def test_upsert_overwrites_pending_placeholder(self):
        """验证 _upsert_assistant_message 的 upsert on_conflict='id' 能覆盖 pending 占位符"""
        from services.handlers.mixins.message_mixin import MessageMixin

        msg_id = str(uuid4())
        conv_id = str(uuid4())

        # 模拟已有 pending 占位符在 DB 中
        db = MagicMock()
        upsert_chain = MagicMock()
        upsert_chain.execute.return_value = MagicMock(data=[{
            "id": msg_id,
            "conversation_id": conv_id,
            "role": "assistant",
            "content": [{"type": "image", "url": "https://oss.example.com/img.png"}],
            "status": "completed",
            "credits_cost": 10,
            "created_at": "2026-03-01T12:00:00+00:00",
        }])

        table_mock = MagicMock()
        table_mock.upsert.return_value = upsert_chain
        db.table.return_value = table_mock

        # 创建 mixin 实例
        mixin = MessageMixin()
        mixin.db = db

        # 调用 upsert
        message, msg_data = mixin._upsert_assistant_message(
            message_id=msg_id,
            conversation_id=conv_id,
            content_dicts=[{"type": "image", "url": "https://oss.example.com/img.png"}],
            status=MessageStatus.COMPLETED,
            credits_cost=10,
            client_task_id="task_123",
            generation_type="image",
            model_id="kie-model",
        )

        # 验证 upsert 使用 on_conflict="id"
        table_mock.upsert.assert_called_once()
        upsert_args = table_mock.upsert.call_args
        assert upsert_args[1].get("on_conflict") == "id" or \
               (len(upsert_args[0]) > 1 and upsert_args[0][1] == "id")

        # 验证消息状态为 completed
        assert message.status == MessageStatus.COMPLETED
        assert msg_data["status"] == "completed"


class TestRegenerateOperation:
    """测试 regenerate 操作与占位符入库的兼容性"""

    @pytest.mark.asyncio
    async def test_regenerate_image_inserts_placeholder(self):
        """Regenerate image 也应将新占位符 insert 到 DB"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db, table_mock, insert_mock = _make_db_mock()

        # regenerate 需要检查原消息状态，mock select().single().execute()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.single.return_value = select_chain
        select_chain.execute.return_value = MagicMock(
            data={"id": "orig_123", "status": "completed", "conversation_id": "conv_123"}
        )
        table_mock.select.return_value = select_chain

        await _handle_regenerate_or_send_operation(
            db=db,
            conversation_id=str(uuid4()),
            operation=MessageOperation.REGENERATE,
            original_message_id="orig_123",
            assistant_message_id=str(uuid4()),
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.IMAGE,
        )

        # 验证 insert 被调用
        table_mock.insert.assert_called_once()


class TestAutoGeneratedId:
    """测试没有传入 assistant_message_id 时自动生成"""

    @pytest.mark.asyncio
    async def test_auto_generate_id_for_image(self):
        """不传 assistant_message_id 时应自动生成 UUID"""
        from api.routes.message import _handle_regenerate_or_send_operation

        db, table_mock, insert_mock = _make_db_mock()

        result_id, result_msg = await _handle_regenerate_or_send_operation(
            db=db,
            conversation_id=str(uuid4()),
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id=None,  # 不传 ID
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.IMAGE,
        )

        # 验证自动生成了 UUID
        assert result_id is not None
        assert len(result_id) == 36  # UUID 长度

        # 验证 insert 中使用了自动生成的 ID
        inserted_data = table_mock.insert.call_args[0][0]
        assert inserted_data["id"] == result_id
