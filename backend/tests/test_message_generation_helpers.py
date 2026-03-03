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
    build_generation_params,
)
from schemas.message import GenerationType, MessageOperation


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
