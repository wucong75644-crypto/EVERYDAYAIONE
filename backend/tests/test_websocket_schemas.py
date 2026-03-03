"""
WebSocket 消息构建函数单元测试

测试 build_image_partial_update 消息格式：
- 成功时 payload 包含 image_index/completed_count/total_count/content_part
- 失败时 payload 包含 error 字段
- 整体消息结构（type/payload/message_id/task_id/conversation_id/timestamp）
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from schemas.websocket import build_image_partial_update, build_message_done


class TestBuildImagePartialUpdate:
    """测试 build_image_partial_update 消息构建"""

    def test_success_payload_structure(self):
        """测试：成功时 payload 包含完整字段"""
        content_part = {
            "type": "image",
            "url": "https://oss/img0.png",
            "width": 1024,
            "height": 1024,
        }

        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=0,
            completed_count=1,
            total_count=4,
            content_part=content_part,
        )

        assert msg["type"] == "image_partial_update"
        assert msg["payload"]["image_index"] == 0
        assert msg["payload"]["completed_count"] == 1
        assert msg["payload"]["total_count"] == 4
        assert msg["payload"]["content_part"] == content_part
        assert "error" not in msg["payload"]

    def test_error_payload_structure(self):
        """测试：失败时 payload 包含 error，content_part 为 None"""
        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=2,
            completed_count=3,
            total_count=4,
            content_part=None,
            error="模型超时",
        )

        assert msg["type"] == "image_partial_update"
        assert msg["payload"]["image_index"] == 2
        assert msg["payload"]["content_part"] is None
        assert msg["payload"]["error"] == "模型超时"
        assert msg["payload"]["completed_count"] == 3
        assert msg["payload"]["total_count"] == 4

    def test_message_top_level_fields(self):
        """测试：顶层字段 task_id / conversation_id / message_id / timestamp"""
        msg = build_image_partial_update(
            task_id="task_abc",
            conversation_id="conv_xyz",
            message_id="msg_123",
            image_index=1,
            completed_count=2,
            total_count=2,
        )

        assert msg["task_id"] == "task_abc"
        assert msg["conversation_id"] == "conv_xyz"
        assert msg["message_id"] == "msg_123"
        assert isinstance(msg["timestamp"], int)
        assert msg["timestamp"] > 0

    def test_no_error_field_when_not_provided(self):
        """测试：不传 error 时 payload 无 error 字段"""
        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=0,
            completed_count=1,
            total_count=1,
            content_part={"type": "image", "url": "https://oss/img.png"},
        )

        assert "error" not in msg["payload"]

    def test_single_image_batch(self):
        """测试：单图批次（num_images=1）的消息格式"""
        content_part = {"type": "image", "url": "https://oss/single.png"}

        msg = build_image_partial_update(
            task_id="task_1",
            conversation_id="conv_1",
            message_id="msg_1",
            image_index=0,
            completed_count=1,
            total_count=1,
            content_part=content_part,
        )

        assert msg["payload"]["image_index"] == 0
        assert msg["payload"]["completed_count"] == 1
        assert msg["payload"]["total_count"] == 1

    def test_image_index_boundary_values(self):
        """测试：image_index 边界值（0 和 3）"""
        for idx in [0, 3]:
            msg = build_image_partial_update(
                task_id="task_1",
                conversation_id="conv_1",
                message_id="msg_1",
                image_index=idx,
                completed_count=idx + 1,
                total_count=4,
            )
            assert msg["payload"]["image_index"] == idx


class TestBuildMessageDone:
    """测试 build_message_done 用于多图 finalize 场景"""

    def test_message_done_with_multi_image_content(self):
        """测试：finalize 后 message_done 包含多图 content"""
        message_data = {
            "id": "msg_1",
            "content": [
                {"type": "image", "url": "https://oss/0.png", "width": 1024, "height": 1024},
                {"type": "image", "url": "https://oss/1.png", "width": 1024, "height": 1024},
                {"type": "image", "url": None, "failed": True, "error": "超时"},
                {"type": "image", "url": "https://oss/3.png", "width": 1024, "height": 1024},
            ],
            "status": "completed",
            "credits_cost": 15,
        }

        msg = build_message_done(
            task_id="task_1",
            conversation_id="conv_1",
            message=message_data,
            credits_consumed=15,
        )

        assert msg["type"] == "message_done"
        assert msg["payload"]["message"]["content"][0]["url"] == "https://oss/0.png"
        assert msg["payload"]["message"]["content"][2]["failed"] is True
        assert msg["payload"]["credits_consumed"] == 15
        assert msg["message_id"] == "msg_1"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
