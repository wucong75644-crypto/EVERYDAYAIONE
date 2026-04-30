"""
message_generation_helpers 单元测试

测试 generation_params 中 num_images 的保存逻辑：
- 图片任务占位符包含 num_images
- 非图片任务不包含 num_images
- 其他图片参数（aspect_ratio, resolution, output_format）也被保存
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from api.routes.message_generation_helpers import (
    handle_regenerate_or_send_operation,
    handle_regenerate_single_operation,
    build_generation_params,
)
from schemas.message import GenerationType, MessageOperation, MessageStatus


# ============ Mock DB ============

class MockGenHelperDB:
    """Mock DB for generation helpers tests"""

    def __init__(self):
        self._inserted = []

    def table(self, name):
        return MockGenTable(self)


class MockGenTable:
    def __init__(self, db):
        self._db = db

    def insert(self, data):
        self._db._inserted.append(data)
        return self

    def select(self, fields="*"):
        return self

    def eq(self, field, value):
        return self

    def single(self):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        result = MagicMock()
        result.data = self._db._inserted[-1] if self._db._inserted else None
        return result


# ============ 测试 ============

class TestPlaceholderNumImages:
    """测试占位符 generation_params 保存 num_images"""

    @pytest.mark.asyncio
    async def test_image_placeholder_includes_num_images(self):
        """测试：图片占位符的 generation_params 包含 num_images"""
        db = MockGenHelperDB()

        await handle_regenerate_or_send_operation(
            db=db,
            conversation_id="conv_1",
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id="msg_1",
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.IMAGE,
            params={"num_images": 4, "aspect_ratio": "1:1", "model": "nano-banana"},
        )

        assert len(db._inserted) == 1
        gen_params = db._inserted[0]["generation_params"]
        assert gen_params["num_images"] == 4
        assert gen_params["type"] == "image"

    @pytest.mark.asyncio
    async def test_image_placeholder_includes_all_image_params(self):
        """测试：图片占位符保存 aspect_ratio/resolution/output_format"""
        db = MockGenHelperDB()

        await handle_regenerate_or_send_operation(
            db=db,
            conversation_id="conv_1",
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id="msg_1",
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.IMAGE,
            params={
                "num_images": 2,
                "aspect_ratio": "16:9",
                "resolution": "2K",
                "output_format": "png",
            },
        )

        gen_params = db._inserted[0]["generation_params"]
        assert gen_params["num_images"] == 2
        assert gen_params["aspect_ratio"] == "16:9"
        assert gen_params["resolution"] == "2K"
        assert gen_params["output_format"] == "png"

    @pytest.mark.asyncio
    async def test_image_placeholder_without_num_images(self):
        """测试：不传 num_images 时 generation_params 不含该字段"""
        db = MockGenHelperDB()

        await handle_regenerate_or_send_operation(
            db=db,
            conversation_id="conv_1",
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id="msg_1",
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.IMAGE,
            params={"aspect_ratio": "1:1"},
        )

        gen_params = db._inserted[0]["generation_params"]
        assert "num_images" not in gen_params
        assert gen_params["aspect_ratio"] == "1:1"

    @pytest.mark.asyncio
    async def test_video_placeholder_no_num_images(self):
        """测试：视频占位符不包含 num_images（视频不支持多图）"""
        db = MockGenHelperDB()

        await handle_regenerate_or_send_operation(
            db=db,
            conversation_id="conv_1",
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id="msg_1",
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.VIDEO,
            params={"num_images": 4},  # 即使传了也不应该保存
        )

        gen_params = db._inserted[0]["generation_params"]
        assert gen_params["type"] == "video"
        assert "num_images" not in gen_params

    @pytest.mark.asyncio
    async def test_single_image_placeholder_num_images_1(self):
        """测试：单图占位符 num_images=1"""
        db = MockGenHelperDB()

        await handle_regenerate_or_send_operation(
            db=db,
            conversation_id="conv_1",
            operation=MessageOperation.SEND,
            original_message_id=None,
            assistant_message_id="msg_1",
            placeholder_created_at=datetime.now(timezone.utc),
            gen_type=GenerationType.IMAGE,
            params={"num_images": 1, "aspect_ratio": "1:1"},
        )

        gen_params = db._inserted[0]["generation_params"]
        assert gen_params["num_images"] == 1


# ============ Mock DB for regenerate_single ============

class MockRegSingleDB:
    """
    Mock DB for handle_regenerate_single_operation tests.

    支持：
    - messages 表：select/eq/single/execute 返回预设消息
    - tasks 表：select/eq/in_/execute 返回预设任务
    - messages 表：update/eq/execute 记录更新
    """

    def __init__(self, message_data=None, task_data=None):
        self._message_data = message_data  # 预设的消息数据
        self._task_data = task_data  # 预设的任务数据（列表）
        self._updated = []  # 记录 update 调用

    def table(self, name):
        return MockRegSingleTable(self, name)


class MockRegSingleTable:
    def __init__(self, db, table_name):
        self._db = db
        self._table_name = table_name
        self._is_update = False
        self._update_data = None

    def select(self, fields="*"):
        return self

    def eq(self, field, value):
        return self

    def in_(self, field, values):
        return self

    def single(self):
        return self

    def maybe_single(self):
        return self

    def update(self, data):
        self._is_update = True
        self._update_data = data
        return self

    def execute(self):
        result = MagicMock()
        if self._is_update:
            self._db._updated.append(self._update_data)
            result.data = self._update_data
        elif self._table_name == "messages":
            result.data = self._db._message_data
        elif self._table_name == "tasks":
            result.data = self._db._task_data
        else:
            result.data = None
        return result


# ============ 测试 regenerate_single ============

class TestHandleRegenerateSingle:
    """测试 handle_regenerate_single_operation 所有分支"""

    @pytest.mark.asyncio
    async def test_missing_original_message_id(self):
        """缺少 original_message_id → 400"""
        db = MockRegSingleDB()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id=None,
                params={"image_index": 0},
            )
        assert exc_info.value.status_code == 400
        assert "original_message_id" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_image_index_no_params(self):
        """params 为 None → 400"""
        db = MockRegSingleDB()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_1",
                params=None,
            )
        assert exc_info.value.status_code == 400
        assert "image_index" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_image_index_in_params(self):
        """params 中没有 image_index → 400"""
        db = MockRegSingleDB()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_1",
                params={"other_key": "value"},
            )
        assert exc_info.value.status_code == 400
        assert "image_index" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_message_not_found(self):
        """消息不存在 → 404"""
        db = MockRegSingleDB(message_data=None)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_not_exist",
                params={"image_index": 0},
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_wrong_conversation_id(self):
        """消息不属于该对话 → 403"""
        db = MockRegSingleDB(message_data={
            "id": "msg_1",
            "conversation_id": "conv_other",
            "status": "completed",
            "content": [{"type": "image", "url": "https://example.com/1.png"}],
            "generation_params": {"type": "image", "num_images": 1},
        })
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_1",
                params={"image_index": 0},
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_status_pending(self):
        """消息状态为 pending → 400"""
        db = MockRegSingleDB(message_data={
            "id": "msg_1",
            "conversation_id": "conv_1",
            "status": "pending",
            "content": [],
            "generation_params": {"type": "image"},
        })
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_1",
                params={"image_index": 0},
            )
        assert exc_info.value.status_code == 400
        assert "已完成或已失败" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_image_index_out_of_range(self):
        """image_index 超出范围 → 400"""
        db = MockRegSingleDB(
            message_data={
                "id": "msg_1",
                "conversation_id": "conv_1",
                "status": "completed",
                "content": [
                    {"type": "image", "url": "https://example.com/1.png"},
                    {"type": "image", "url": "https://example.com/2.png"},
                ],
                "generation_params": {"type": "image", "num_images": 2},
            },
            task_data=None,
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_1",
                params={"image_index": 5},
            )
        assert exc_info.value.status_code == 400
        assert "超出范围" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_image_index_negative(self):
        """image_index 为负数 → 400"""
        db = MockRegSingleDB(
            message_data={
                "id": "msg_1",
                "conversation_id": "conv_1",
                "status": "completed",
                "content": [{"type": "image", "url": "https://example.com/1.png"}],
                "generation_params": {"type": "image", "num_images": 1},
            },
            task_data=None,
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_1",
                params={"image_index": -1},
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_task_in_progress_conflict(self):
        """已有进行中的任务 → 409"""
        db = MockRegSingleDB(
            message_data={
                "id": "msg_1",
                "conversation_id": "conv_1",
                "status": "completed",
                "content": [
                    {"type": "image", "url": "https://example.com/1.png"},
                    {"type": "image", "url": "https://example.com/2.png"},
                ],
                "generation_params": {"type": "image", "num_images": 2},
            },
            task_data=[{"id": "task_running"}],  # 有进行中的任务
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await handle_regenerate_single_operation(
                db=db,
                conversation_id="conv_1",
                original_message_id="msg_1",
                params={"image_index": 0},
            )
        assert exc_info.value.status_code == 409
        assert "正在重新生成" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_success_completed_message(self):
        """成功：已完成消息的单图重新生成"""
        content = [
            {"type": "image", "url": "https://example.com/1.png"},
            {"type": "image", "url": "https://example.com/2.png"},
            {"type": "image", "url": "https://example.com/3.png"},
        ]
        db = MockRegSingleDB(
            message_data={
                "id": "msg_1",
                "conversation_id": "conv_1",
                "status": "completed",
                "content": content,
                "generation_params": {"type": "image", "num_images": 3},
            },
            task_data=None,  # 无进行中任务
        )

        msg_id, msg = await handle_regenerate_single_operation(
            db=db,
            conversation_id="conv_1",
            original_message_id="msg_1",
            params={"image_index": 1},
        )

        # 返回原消息 ID
        assert msg_id == "msg_1"
        # 消息状态保持 completed
        assert msg.status == MessageStatus.COMPLETED
        # content[1] 已被置为 null 占位
        assert len(db._updated) == 1
        updated_content = db._updated[0]["content"]
        assert updated_content[0]["url"] == "https://example.com/1.png"
        assert updated_content[1] == {"type": "image", "url": None}
        assert updated_content[2]["url"] == "https://example.com/3.png"

    @pytest.mark.asyncio
    async def test_success_failed_message(self):
        """成功：已失败消息的单图重新生成"""
        content = [
            {"type": "image", "url": "https://example.com/1.png"},
            {"type": "image", "url": None},
        ]
        db = MockRegSingleDB(
            message_data={
                "id": "msg_2",
                "conversation_id": "conv_1",
                "status": "failed",
                "content": content,
                "generation_params": {"type": "image", "num_images": 2},
            },
            task_data=None,
        )

        msg_id, msg = await handle_regenerate_single_operation(
            db=db,
            conversation_id="conv_1",
            original_message_id="msg_2",
            params={"image_index": 1},
        )

        assert msg_id == "msg_2"
        assert msg.status == MessageStatus.FAILED
        assert msg.generation_params is not None
        assert msg.generation_params.type == GenerationType.IMAGE

    @pytest.mark.asyncio
    async def test_success_preserves_generation_params(self):
        """成功：返回的 Message 包含原始 generation_params"""
        db = MockRegSingleDB(
            message_data={
                "id": "msg_3",
                "conversation_id": "conv_1",
                "status": "completed",
                "content": [{"type": "image", "url": "https://example.com/1.png"}],
                "generation_params": {"type": "image", "num_images": 1},
            },
            task_data=None,
        )

        _, msg = await handle_regenerate_single_operation(
            db=db,
            conversation_id="conv_1",
            original_message_id="msg_3",
            params={"image_index": 0},
        )

        assert msg.generation_params is not None
        assert msg.generation_params.type == GenerationType.IMAGE


class TestBuildGenerationParams:
    """测试 build_generation_params 公共函数"""

    def test_basic_type(self):
        """测试：基本类型字段"""
        result = build_generation_params(GenerationType.IMAGE)
        assert result == {"type": "image"}

    def test_with_model(self):
        """测试：包含模型"""
        result = build_generation_params(GenerationType.IMAGE, model="nano-banana")
        assert result["type"] == "image"
        assert result["model"] == "nano-banana"

    def test_with_params(self):
        """测试：包含额外参数"""
        result = build_generation_params(
            GenerationType.IMAGE,
            params={"num_images": 4, "aspect_ratio": "1:1"},
        )
        assert result["num_images"] == 4
        assert result["aspect_ratio"] == "1:1"

    def test_chat_type(self):
        """测试：chat 类型"""
        result = build_generation_params(GenerationType.CHAT)
        assert result == {"type": "chat"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
