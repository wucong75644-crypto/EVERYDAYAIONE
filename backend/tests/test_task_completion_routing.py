"""
TaskCompletionService 分流逻辑单元测试

测试 _handle_success / _handle_failure 的分流条件：
- 图片任务（有 batch_id）→ BatchCompletionService
- 图片任务（无 batch_id，历史数据）→ ImageHandler.on_complete
- 视频任务 → VideoHandler.on_complete
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.task_completion_service import TaskCompletionService
from services.adapters.base import ImageGenerateResult, VideoGenerateResult, TaskStatus


# ============ Mock DB ============

class MockRoutingDB:
    """分流测试用 Mock DB"""

    def table(self, name: str):
        return MockRoutingTable()

    def rpc(self, fn_name: str, params=None):
        mock = MagicMock()
        mock.execute.return_value = MagicMock(data={"success": True})
        return mock


class MockRoutingTable:
    def select(self, fields="*"):
        return self

    def insert(self, data):
        return self

    def update(self, data):
        return self

    def upsert(self, data, on_conflict=None):
        result = MagicMock()
        result.data = [data]
        return self

    def eq(self, field, value):
        return self

    def in_(self, field, values):
        return self

    def order(self, col, **kwargs):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        result = MagicMock()
        result.data = []
        return result


# ============ 测试辅助 ============

def make_image_task(batch_id=None):
    """创建图片任务"""
    task = {
        "external_task_id": "ext_img_1",
        "type": "image",
        "status": "pending",
        "user_id": "user_1",
        "conversation_id": "conv_1",
        "placeholder_message_id": "msg_1",
        "model_id": "nano-banana",
        "request_params": {"aspect_ratio": "1:1"},
        "credit_transaction_id": "tx_1",
        "credits_locked": 5,
        "client_task_id": "client_1",
    }
    if batch_id is not None:
        task["batch_id"] = batch_id
        task["image_index"] = 0
    return task


def make_video_task():
    """创建视频任务"""
    return {
        "external_task_id": "ext_vid_1",
        "type": "video",
        "status": "pending",
        "user_id": "user_1",
        "conversation_id": "conv_1",
        "placeholder_message_id": "msg_2",
        "model_id": "video-model",
        "request_params": {"n_frames": "10"},
        "credit_transaction_id": "tx_2",
        "credits_locked": 10,
        "client_task_id": "client_2",
    }


def make_success_result(task_id: str = "ext_1"):
    """创建图片成功结果"""
    return ImageGenerateResult(
        task_id=task_id,
        status=TaskStatus.SUCCESS,
        image_urls=["https://cdn.example.com/img.png"],
    )


def make_video_success_result(task_id: str = "ext_1"):
    """创建视频成功结果"""
    return VideoGenerateResult(
        task_id=task_id,
        status=TaskStatus.SUCCESS,
        video_url="https://cdn.example.com/video.mp4",
    )


def make_failure_result(task_id: str = "ext_1"):
    """创建失败结果"""
    return ImageGenerateResult(
        task_id=task_id,
        status=TaskStatus.FAILED,
        fail_code="GENERATION_FAILED",
        fail_msg="模型超时",
    )


def make_video_failure_result(task_id: str = "ext_1"):
    """创建视频失败结果"""
    return VideoGenerateResult(
        task_id=task_id,
        status=TaskStatus.FAILED,
        fail_code="VIDEO_FAILED",
        fail_msg="视频生成超时",
    )


# ============ 测试 ============

class TestHandleSuccessRouting:
    """测试 _handle_success 分流逻辑"""

    @pytest.fixture
    def service(self):
        return TaskCompletionService(MockRoutingDB())

    @pytest.mark.asyncio
    @patch("services.task_completion_service.TaskCompletionService._upload_urls_to_oss")
    @patch("services.batch_completion_service.BatchCompletionService.handle_image_complete")
    async def test_image_with_batch_id_routes_to_batch_service(
        self, mock_batch_complete, mock_oss, service
    ):
        """测试：图片任务 + batch_id → BatchCompletionService"""
        mock_oss.return_value = ["https://oss/img.png"]
        mock_batch_complete.return_value = True

        task = make_image_task(batch_id="batch_abc")
        result = make_success_result(task["external_task_id"])

        success = await service._handle_success(task, result)

        assert success is True
        mock_batch_complete.assert_called_once()
        # 验证传递了正确的 task 和 content_parts
        call_args = mock_batch_complete.call_args
        assert call_args[0][0]["batch_id"] == "batch_abc"

    @pytest.mark.asyncio
    @patch("services.task_completion_service.TaskCompletionService._upload_urls_to_oss")
    @patch("services.handlers.image_handler.ImageHandler.on_complete")
    async def test_image_without_batch_id_routes_to_handler(
        self, mock_on_complete, mock_oss, service
    ):
        """测试：图片任务 + 无 batch_id（历史数据）→ ImageHandler"""
        mock_oss.return_value = ["https://oss/img.png"]
        mock_on_complete.return_value = None

        task = make_image_task(batch_id=None)  # 无 batch_id
        result = make_success_result(task["external_task_id"])

        success = await service._handle_success(task, result)

        assert success is True
        mock_on_complete.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.task_completion_service.TaskCompletionService._upload_urls_to_oss")
    @patch("services.handlers.video_handler.VideoHandler.on_complete")
    async def test_video_task_routes_to_video_handler(
        self, mock_on_complete, mock_oss, service
    ):
        """测试：视频任务 → VideoHandler（不走批次处理）"""
        mock_oss.return_value = ["https://oss/video.mp4"]
        mock_on_complete.return_value = None

        task = make_video_task()
        result = make_video_success_result(task["external_task_id"])

        success = await service._handle_success(task, result)

        assert success is True
        mock_on_complete.assert_called_once()


class TestHandleFailureRouting:
    """测试 _handle_failure 分流逻辑"""

    @pytest.fixture
    def service(self):
        return TaskCompletionService(MockRoutingDB())

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.BatchCompletionService.handle_image_failure")
    async def test_image_failure_with_batch_id_routes_to_batch(
        self, mock_batch_failure, service
    ):
        """测试：图片失败 + batch_id → BatchCompletionService"""
        mock_batch_failure.return_value = True

        task = make_image_task(batch_id="batch_xyz")
        result = make_failure_result(task["external_task_id"])

        success = await service._handle_failure(task, result)

        assert success is True
        mock_batch_failure.assert_called_once()
        call_args = mock_batch_failure.call_args
        assert call_args[1]["error_code"] == "GENERATION_FAILED"
        assert call_args[1]["error_message"] == "模型超时"

    @pytest.mark.asyncio
    @patch("services.handlers.image_handler.ImageHandler.on_error")
    async def test_image_failure_without_batch_id_routes_to_handler(
        self, mock_on_error, service
    ):
        """测试：图片失败 + 无 batch_id → ImageHandler"""
        mock_on_error.return_value = None

        task = make_image_task(batch_id=None)
        result = make_failure_result(task["external_task_id"])

        success = await service._handle_failure(task, result)

        assert success is True
        mock_on_error.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.handlers.video_handler.VideoHandler.on_error")
    async def test_video_failure_routes_to_video_handler(
        self, mock_on_error, service
    ):
        """测试：视频失败 → VideoHandler"""
        mock_on_error.return_value = None

        task = make_video_task()
        result = make_video_failure_result(task["external_task_id"])

        success = await service._handle_failure(task, result)

        assert success is True
        mock_on_error.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.BatchCompletionService.handle_image_failure")
    async def test_failure_uses_default_error_when_missing(
        self, mock_batch_failure, service
    ):
        """测试：无 fail_code/fail_msg 时使用默认值"""
        mock_batch_failure.return_value = True

        task = make_image_task(batch_id="batch_1")
        result = ImageGenerateResult(
            task_id=task["external_task_id"],
            status=TaskStatus.FAILED,
            fail_code=None,
            fail_msg=None,
        )

        await service._handle_failure(task, result)

        call_args = mock_batch_failure.call_args
        assert call_args[1]["error_code"] == "UNKNOWN"
        assert call_args[1]["error_message"] == "任务失败"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
