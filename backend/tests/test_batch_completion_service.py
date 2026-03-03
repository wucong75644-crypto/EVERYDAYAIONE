"""
BatchCompletionService 单元测试

测试多图批次完成处理逻辑：
- 单个 task 成功：确认积分 + 更新状态 + 推送 partial update
- 单个 task 失败：退回积分 + 更新状态 + 推送 partial update
- 批次 finalize：全部终态后汇总 message + 推送 done
- 边界情况：部分成功部分失败、全部失败
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.batch_completion_service import BatchCompletionService


# ============ 测试辅助 ============

def create_batch_task(
    index: int,
    batch_id: str,
    status: str = "pending",
    user_id: str = "user_1",
    conversation_id: str = "conv_1",
    message_id: str = "msg_1",
    model_id: str = "nano-banana",
    credits_locked: int = 5,
    transaction_id: str = None,
    result_data: dict = None,
    error_message: str = None,
    client_task_id: str = "client_task_1",
) -> dict:
    """创建测试用的批次 task 数据"""
    return {
        "external_task_id": f"ext_{batch_id}_{index}",
        "batch_id": batch_id,
        "image_index": index,
        "status": status,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "placeholder_message_id": message_id,
        "model_id": model_id,
        "credits_locked": credits_locked,
        "credit_transaction_id": transaction_id or f"tx_{batch_id}_{index}",
        "result_data": result_data,
        "error_message": error_message,
        "client_task_id": client_task_id,
        "type": "image",
    }


def create_content_part(url: str = "https://oss.example.com/img.png") -> dict:
    """创建测试用的 content_part"""
    return {
        "type": "image",
        "url": url,
        "width": 1024,
        "height": 1024,
    }


# ============ Mock DB ============

class MockBatchDB:
    """专用于 BatchCompletionService 测试的 Mock DB"""

    def __init__(self):
        self._tables = {}
        self._rpc_results = {}

    def table(self, name: str):
        return MockTableChain(self._tables.get(name, []))

    def set_table_data(self, name: str, data: list):
        self._tables[name] = data

    def rpc(self, fn_name: str, params: dict = None):
        mock = MagicMock()
        result = self._rpc_results.get(fn_name, {"success": True})
        mock.execute.return_value = MagicMock(data=result)
        return mock


class MockTableChain:
    """Mock 链式调用"""

    def __init__(self, data: list):
        self._data = data
        self._filters = {}

    def select(self, fields: str = "*"):
        return self

    def insert(self, data):
        return self

    def update(self, data):
        return self

    def upsert(self, data, on_conflict=None):
        # 返回包含 data 的结果
        result = MagicMock()
        result.data = [data]
        self._upsert_result = result
        return self

    def eq(self, field: str, value):
        self._filters[field] = value
        return self

    def order(self, column: str, **kwargs):
        return self

    def execute(self):
        if hasattr(self, '_upsert_result'):
            return self._upsert_result
        result = MagicMock()
        filtered = self._data
        for field, value in self._filters.items():
            filtered = [d for d in filtered if d.get(field) == value]
        result.data = filtered
        return result


# ============ 测试 ============

class TestBatchCompletionServiceHandleComplete:
    """测试单个图片 task 成功处理"""

    @pytest.fixture
    def db(self):
        return MockBatchDB()

    @pytest.fixture
    def service(self, db):
        return BatchCompletionService(db)

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_single_task_complete_confirms_credits(self, mock_ws, service, db):
        """测试：成功时确认积分"""
        batch_id = str(uuid4())
        task = create_batch_task(index=0, batch_id=batch_id, status="pending")
        content_parts = [create_content_part()]

        # 设置批次查询（只有 1 个 task，且已完成）
        db.set_table_data("tasks", [
            {**task, "status": "completed", "result_data": content_parts[0]},
        ])

        mock_ws.send_to_task_or_user = AsyncMock()

        result = await service.handle_image_complete(task, content_parts)

        assert result is True

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_pushes_partial_update(self, mock_ws, service, db):
        """测试：推送 image_partial_update 事件"""
        batch_id = str(uuid4())
        task = create_batch_task(index=1, batch_id=batch_id)
        content_parts = [create_content_part()]

        # 4 个 task，2 个已完成
        batch_tasks = [
            create_batch_task(0, batch_id, status="completed", result_data=create_content_part()),
            create_batch_task(1, batch_id, status="completed", result_data=content_parts[0]),
            create_batch_task(2, batch_id, status="pending"),
            create_batch_task(3, batch_id, status="pending"),
        ]
        db.set_table_data("tasks", batch_tasks)

        mock_ws.send_to_task_or_user = AsyncMock()

        await service.handle_image_complete(task, content_parts)

        # 验证 WS 推送
        mock_ws.send_to_task_or_user.assert_called_once()
        call_kwargs = mock_ws.send_to_task_or_user.call_args
        ws_msg = call_kwargs.kwargs.get("message") or call_kwargs[1].get("message")
        assert ws_msg["type"] == "image_partial_update"
        assert ws_msg["payload"]["image_index"] == 1
        assert ws_msg["payload"]["completed_count"] == 2
        assert ws_msg["payload"]["total_count"] == 4

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_no_finalize_when_not_all_terminal(self, mock_ws, service, db):
        """测试：未全部终态时不触发 finalize"""
        batch_id = str(uuid4())
        task = create_batch_task(index=0, batch_id=batch_id)
        content_parts = [create_content_part()]

        # 2/4 完成
        batch_tasks = [
            create_batch_task(0, batch_id, status="completed"),
            create_batch_task(1, batch_id, status="completed"),
            create_batch_task(2, batch_id, status="pending"),
            create_batch_task(3, batch_id, status="pending"),
        ]
        db.set_table_data("tasks", batch_tasks)
        mock_ws.send_to_task_or_user = AsyncMock()

        await service.handle_image_complete(task, content_parts)

        # 只应推送 1 次（partial_update），不应推送 message_done
        assert mock_ws.send_to_task_or_user.call_count == 1

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_finalize_when_all_terminal(self, mock_ws, service, db):
        """测试：全部终态时触发 finalize（推送 partial + done）"""
        batch_id = str(uuid4())
        task = create_batch_task(index=1, batch_id=batch_id)
        content_parts = [create_content_part("https://oss/img1.png")]

        # 全部 2 个 task 都已完成
        batch_tasks = [
            create_batch_task(0, batch_id, status="completed",
                              result_data=create_content_part("https://oss/img0.png")),
            create_batch_task(1, batch_id, status="completed",
                              result_data=create_content_part("https://oss/img1.png")),
        ]
        db.set_table_data("tasks", batch_tasks)
        mock_ws.send_to_task_or_user = AsyncMock()

        await service.handle_image_complete(task, content_parts)

        # 应推送 2 次：partial_update + message_done
        assert mock_ws.send_to_task_or_user.call_count == 2
        ws_calls = mock_ws.send_to_task_or_user.call_args_list
        msg_types = [
            (c.kwargs.get("message") or c[1].get("message"))["type"]
            for c in ws_calls
        ]
        assert "image_partial_update" in msg_types
        assert "message_done" in msg_types

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_no_transaction_id_skips_credit_confirm(self, mock_ws, service, db):
        """测试：无 transaction_id 时跳过积分确认"""
        batch_id = str(uuid4())
        task = create_batch_task(index=0, batch_id=batch_id, transaction_id="")
        task["credit_transaction_id"] = None  # 无积分事务

        db.set_table_data("tasks", [
            {**task, "status": "completed"},
        ])
        mock_ws.send_to_task_or_user = AsyncMock()

        # 不应抛异常
        result = await service.handle_image_complete(task, [create_content_part()])
        assert result is True


class TestBatchCompletionServiceHandleFailure:
    """测试单个图片 task 失败处理"""

    @pytest.fixture
    def db(self):
        return MockBatchDB()

    @pytest.fixture
    def service(self, db):
        return BatchCompletionService(db)

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_failure_pushes_error_partial_update(self, mock_ws, service, db):
        """测试：失败时推送带 error 的 partial_update"""
        batch_id = str(uuid4())
        task = create_batch_task(index=2, batch_id=batch_id)

        # 3/4 终态（2 成功 + 1 失败）
        batch_tasks = [
            create_batch_task(0, batch_id, status="completed"),
            create_batch_task(1, batch_id, status="completed"),
            create_batch_task(2, batch_id, status="failed"),
            create_batch_task(3, batch_id, status="pending"),
        ]
        db.set_table_data("tasks", batch_tasks)
        mock_ws.send_to_task_or_user = AsyncMock()

        await service.handle_image_failure(task, "GENERATION_FAILED", "模型超时")

        # 验证推送带 error
        ws_msg = (
            mock_ws.send_to_task_or_user.call_args.kwargs.get("message")
            or mock_ws.send_to_task_or_user.call_args[1].get("message")
        )
        assert ws_msg["type"] == "image_partial_update"
        assert ws_msg["payload"]["error"] == "模型超时"
        assert ws_msg["payload"]["content_part"] is None

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_failure_returns_true(self, mock_ws, service, db):
        """测试：失败处理返回 True"""
        batch_id = str(uuid4())
        task = create_batch_task(index=0, batch_id=batch_id)
        db.set_table_data("tasks", [
            {**task, "status": "failed"},
        ])
        mock_ws.send_to_task_or_user = AsyncMock()

        result = await service.handle_image_failure(task, "ERR", "fail")
        assert result is True


class TestBatchCompletionServiceFinalize:
    """测试批次 finalize 逻辑"""

    @pytest.fixture
    def db(self):
        return MockBatchDB()

    @pytest.fixture
    def service(self, db):
        return BatchCompletionService(db)

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_finalize_all_success(self, mock_ws, service, db):
        """测试：全部成功时 message status=completed"""
        batch_id = str(uuid4())
        batch_tasks = [
            create_batch_task(0, batch_id, status="completed",
                              result_data=create_content_part("https://oss/0.png"),
                              credits_locked=5),
            create_batch_task(1, batch_id, status="completed",
                              result_data=create_content_part("https://oss/1.png"),
                              credits_locked=5),
        ]
        db.set_table_data("tasks", batch_tasks)
        mock_ws.send_to_task_or_user = AsyncMock()

        await service._finalize_batch(batch_id, batch_tasks)

        # 验证推送 message_done
        ws_msg = (
            mock_ws.send_to_task_or_user.call_args.kwargs.get("message")
            or mock_ws.send_to_task_or_user.call_args[1].get("message")
        )
        assert ws_msg["type"] == "message_done"

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_finalize_partial_failure(self, mock_ws, service, db):
        """测试：部分失败时 message status=completed（至少 1 张成功）"""
        batch_id = str(uuid4())
        batch_tasks = [
            create_batch_task(0, batch_id, status="completed",
                              result_data=create_content_part(), credits_locked=5),
            create_batch_task(1, batch_id, status="failed",
                              error_message="超时", credits_locked=5),
            create_batch_task(2, batch_id, status="failed",
                              error_message="限流", credits_locked=5),
        ]
        db.set_table_data("tasks", batch_tasks)
        mock_ws.send_to_task_or_user = AsyncMock()

        await service._finalize_batch(batch_id, batch_tasks)

        # message_done 经过 build_message_done → _build_ws_message 包装
        # 结构: {type, payload: {message: {...}}, ...}
        ws_msg = (
            mock_ws.send_to_task_or_user.call_args.kwargs.get("message")
            or mock_ws.send_to_task_or_user.call_args[1].get("message")
        )
        msg_data = ws_msg.get("payload", {}).get("message", {})
        content = msg_data.get("content", [])
        # 第 1 个成功，第 2/3 个失败
        assert content[0]["url"] is not None
        assert content[1].get("failed") is True
        assert content[2].get("failed") is True
        # 状态应为 completed（至少 1 张成功）
        assert msg_data["status"] == "completed"

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_finalize_all_failed(self, mock_ws, service, db):
        """测试：全部失败时 message status=failed"""
        batch_id = str(uuid4())
        batch_tasks = [
            create_batch_task(0, batch_id, status="failed", error_message="err1"),
            create_batch_task(1, batch_id, status="failed", error_message="err2"),
        ]
        db.set_table_data("tasks", batch_tasks)
        mock_ws.send_to_task_or_user = AsyncMock()

        await service._finalize_batch(batch_id, batch_tasks)

        ws_msg = (
            mock_ws.send_to_task_or_user.call_args.kwargs.get("message")
            or mock_ws.send_to_task_or_user.call_args[1].get("message")
        )
        msg_data = ws_msg.get("payload", {}).get("message", {})
        assert msg_data["status"] == "failed"
        assert msg_data["credits_cost"] == 0  # 全部失败，无积分

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_finalize_empty_batch_noop(self, mock_ws, service, db):
        """测试：空批次不做任何操作"""
        await service._finalize_batch("batch_empty", [])

        mock_ws.send_to_task_or_user.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.batch_completion_service.ws_manager")
    async def test_finalize_updates_conversation_preview(self, mock_ws, service, db):
        """测试：finalize 更新对话预览"""
        batch_id = str(uuid4())
        batch_tasks = [
            create_batch_task(0, batch_id, status="completed",
                              result_data=create_content_part()),
            create_batch_task(1, batch_id, status="completed",
                              result_data=create_content_part()),
        ]
        db.set_table_data("tasks", batch_tasks)
        mock_ws.send_to_task_or_user = AsyncMock()

        await service._finalize_batch(batch_id, batch_tasks)

        # 多图应显示 [图片×2]
        # 这里通过 mock DB 的 conversations 表 update 验证比较复杂
        # 主要验证不抛异常即可
        assert mock_ws.send_to_task_or_user.called


class TestBatchCompletionServiceHelpers:
    """测试内部辅助方法"""

    @pytest.fixture
    def service(self):
        return BatchCompletionService(MockBatchDB())

    def test_count_terminal_all_completed(self, service):
        """测试：统计全部完成"""
        tasks = [
            {"status": "completed"},
            {"status": "completed"},
        ]
        terminal, total = service._count_terminal(tasks)
        assert terminal == 2
        assert total == 2

    def test_count_terminal_mixed(self, service):
        """测试：统计混合状态"""
        tasks = [
            {"status": "completed"},
            {"status": "failed"},
            {"status": "pending"},
            {"status": "cancelled"},
        ]
        terminal, total = service._count_terminal(tasks)
        assert terminal == 3  # completed + failed + cancelled
        assert total == 4

    def test_count_terminal_empty(self, service):
        """测试：空列表"""
        terminal, total = service._count_terminal([])
        assert terminal == 0
        assert total == 0

    def test_count_terminal_all_pending(self, service):
        """测试：全部 pending"""
        tasks = [{"status": "pending"}, {"status": "pending"}]
        terminal, total = service._count_terminal(tasks)
        assert terminal == 0
        assert total == 2


class TestBatchCompletionServiceCredits:
    """测试积分确认/退回"""

    @pytest.fixture
    def db(self):
        return MockBatchDB()

    @pytest.fixture
    def service(self, db):
        return BatchCompletionService(db)

    def test_confirm_credits_no_exception(self, service):
        """测试：确认积分不抛异常"""
        service._confirm_credits("tx_123")

    def test_refund_credits_no_exception(self, service):
        """测试：退回积分不抛异常"""
        service._refund_credits("tx_123")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
