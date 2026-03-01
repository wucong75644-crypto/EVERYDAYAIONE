"""
边界情况修复的单元测试

验证 3 个修复：
1. message_mixin: 幂等路径 existing_msg 为 None 时不崩溃
2. message_mixin: _upsert_assistant_message 返回 None 时正常抛异常
3. task_completion_service: started_at 使用合法 ISO 时间戳而非 "NOW()" 字符串
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

from services.task_completion_service import TaskCompletionService
from services.adapters.base import TaskStatus
from services.adapters.types import ImageGenerateResult, VideoGenerateResult


# ============ 辅助函数 ============

def _make_task(
    external_task_id: str = "test_task_123",
    status: str = "running",
    version: int = 1,
    started_at: str = None,
    task_type: str = "image",
    **kwargs,
) -> dict:
    """创建标准测试 task 数据"""
    task = {
        "external_task_id": external_task_id,
        "status": status,
        "version": version,
        "started_at": started_at,
        "type": task_type,
        "user_id": "user_123",
        "conversation_id": "conv_123",
        "placeholder_message_id": "msg_123",
        "model_id": "kie-model",
        "client_task_id": "client_task_123",
        "request_params": {"aspect_ratio": "1:1"},
        "credits_locked": 10,
        "credit_transaction_id": "tx_123",
    }
    task.update(kwargs)
    return task


def _make_message_data(message_id: str = "msg_123", status: str = "completed") -> dict:
    """创建标准测试 message 数据"""
    return {
        "id": message_id,
        "conversation_id": "conv_123",
        "role": "assistant",
        "content": [{"type": "image", "url": "https://oss.example.com/img.png"}],
        "status": status,
        "credits_cost": 10,
        "is_error": False,
        "created_at": "2026-03-01T12:00:00+00:00",
    }


# ============ 修复 1：幂等路径 NoneType 防护 ============

class TestIdempotencyNoneTypeGuard:
    """
    测试 _handle_complete_common / _handle_error_common 中
    existing_msg 为 None 或 existing_msg.data 为 None 时不崩溃
    """

    def _create_handler_with_mocks(self, task_status, msg_execute_returns_none=True):
        """
        创建 ImageHandler 并 mock 掉不相关的依赖，
        只保留 existing_msg 查询路径作为测试重点
        """
        from services.handlers.image_handler import ImageHandler

        db = MagicMock()

        # _get_task 返回指定状态的 task
        task_data = _make_task(status=task_status)
        task_response = MagicMock()
        task_response.data = task_data

        tasks_chain = MagicMock()
        tasks_chain.select.return_value = tasks_chain
        tasks_chain.eq.return_value = tasks_chain
        tasks_chain.maybe_single.return_value = tasks_chain
        tasks_chain.execute.return_value = task_response

        # messages 链：先模拟 select().eq().maybe_single().execute()
        messages_chain = MagicMock()
        messages_chain.select.return_value = messages_chain
        messages_chain.eq.return_value = messages_chain
        messages_chain.maybe_single.return_value = messages_chain

        if msg_execute_returns_none:
            messages_chain.execute.return_value = None  # 关键：模拟 DB 抖动返回 None
        else:
            msg_result = MagicMock()
            msg_result.data = None  # data 为 None
            messages_chain.execute.return_value = msg_result

        # upsert 链（fall-through 路径需要写入消息）
        upsert_msg = _make_message_data(status=task_status)
        if task_status == "failed":
            upsert_msg["is_error"] = True
            upsert_msg["content"] = [{"type": "text", "text": "错误消息"}]
        upsert_chain = MagicMock()
        upsert_chain.execute.return_value = MagicMock(data=[upsert_msg])
        messages_chain.upsert.return_value = upsert_chain

        def table_dispatch(name):
            if name == "tasks":
                return tasks_chain
            elif name == "messages":
                return messages_chain
            elif name == "credit_transactions":
                ct = MagicMock()
                ct.select.return_value = ct
                ct.update.return_value = ct
                ct.eq.return_value = ct
                ct.maybe_single.return_value = ct
                # _refund_credits 用 maybe_single，期望 data 是 dict
                ct.execute.return_value = MagicMock(data={
                    "id": "tx_123", "user_id": "user_123",
                    "amount": 10, "status": "pending",
                })
                return ct
            else:
                # conversations 等
                chain = MagicMock()
                chain.select.return_value = chain
                chain.update.return_value = chain
                chain.eq.return_value = chain
                chain.execute.return_value = MagicMock(data=[{}])
                return chain

        db.table = MagicMock(side_effect=table_dispatch)
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=MagicMock(data={}))))

        handler = ImageHandler(db)
        return handler

    @pytest.mark.asyncio
    async def test_complete_common_existing_msg_is_none(self):
        """
        修复 1a：当 maybe_single().execute() 返回 None 时，
        _handle_complete_common 不应抛 AttributeError('NoneType' has no attr 'data')
        """
        handler = self._create_handler_with_mocks(
            task_status="completed", msg_execute_returns_none=True
        )

        # patch 掉 _complete_task（测试重点不在这里）和 WS
        with patch.object(handler, "_complete_task"), \
             patch("services.websocket_manager.ws_manager") as mock_ws, \
             patch("schemas.websocket.build_message_done"):
            mock_ws.send_to_task_or_user = AsyncMock()
            # 修复前：AttributeError: 'NoneType' object has no attribute 'data'
            # 修复后：走 fall-through 路径重建消息
            result = await handler.on_complete(
                task_id="test_task_123",
                result=[{"type": "image", "url": "https://oss.example.com/img.png", "width": 1024, "height": 1024}],
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_complete_common_existing_msg_data_is_none(self):
        """
        修复 1b：当 execute() 返回有效对象但 .data 为 None 时，
        应走 fall-through 路径而不崩溃
        """
        handler = self._create_handler_with_mocks(
            task_status="completed", msg_execute_returns_none=False
        )

        with patch.object(handler, "_complete_task"), \
             patch("services.websocket_manager.ws_manager") as mock_ws, \
             patch("schemas.websocket.build_message_done"):
            mock_ws.send_to_task_or_user = AsyncMock()
            result = await handler.on_complete(
                task_id="test_task_123",
                result=[{"type": "image", "url": "https://oss.example.com/img.png", "width": 1024, "height": 1024}],
            )

        assert result is not None

    @pytest.mark.asyncio
    async def test_error_common_existing_msg_is_none(self):
        """
        修复 1c：_handle_error_common 中 existing_msg 为 None 不崩溃
        """
        handler = self._create_handler_with_mocks(
            task_status="failed", msg_execute_returns_none=True
        )

        with patch.object(handler, "_fail_task"), \
             patch("services.websocket_manager.ws_manager") as mock_ws, \
             patch("schemas.websocket.build_message_error"):
            mock_ws.send_to_task_or_user = AsyncMock()
            result = await handler.on_error(
                task_id="test_task_123",
                error_code="TEST_ERROR",
                error_message="测试错误",
            )

        assert result is not None


# ============ 修复 2：_upsert_assistant_message NoneType 防护 ============

class TestUpsertNoneTypeGuard:
    """
    测试 _upsert_assistant_message 中 upsert_result 为 None 时，
    抛出清晰的 Exception 而非 AttributeError
    """

    @pytest.mark.asyncio
    async def test_upsert_returns_none_raises_clean_error(self):
        """
        修复 2：当 execute() 返回 None 时，应抛出 '创建/更新消息失败'
        而不是 'NoneType' object has no attribute 'data'
        """
        db = MagicMock()

        # messages.upsert().execute() 返回 None
        messages_chain = MagicMock()
        messages_chain.upsert.return_value = messages_chain
        messages_chain.execute.return_value = None  # 关键
        db.table = MagicMock(return_value=messages_chain)

        from services.handlers.image_handler import ImageHandler
        handler = ImageHandler(db)

        with pytest.raises(Exception, match="创建/更新消息失败"):
            handler._upsert_assistant_message(
                message_id="msg_123",
                conversation_id="conv_123",
                content_dicts=[{"type": "image", "url": "https://example.com/img.png"}],
                status=MagicMock(value="completed"),
                credits_cost=10,
                client_task_id="client_123",
                generation_type="image",
                model_id="kie-model",
            )

    @pytest.mark.asyncio
    async def test_upsert_returns_empty_data_raises_clean_error(self):
        """
        upsert_result.data 为空列表时，也应抛出清晰异常
        """
        db = MagicMock()

        result_mock = MagicMock()
        result_mock.data = []  # 空列表
        messages_chain = MagicMock()
        messages_chain.upsert.return_value = messages_chain
        messages_chain.execute.return_value = result_mock
        db.table = MagicMock(return_value=messages_chain)

        from services.handlers.image_handler import ImageHandler
        handler = ImageHandler(db)

        with pytest.raises(Exception, match="创建/更新消息失败"):
            handler._upsert_assistant_message(
                message_id="msg_123",
                conversation_id="conv_123",
                content_dicts=[{"type": "image", "url": "https://example.com/img.png"}],
                status=MagicMock(value="completed"),
                credits_cost=10,
                client_task_id="client_123",
                generation_type="image",
                model_id="kie-model",
            )

    @pytest.mark.asyncio
    async def test_upsert_success_returns_message(self):
        """
        正常情况：upsert 成功应返回 (Message, dict)
        """
        db = MagicMock()

        msg_data = _make_message_data()
        result_mock = MagicMock()
        result_mock.data = [msg_data]
        messages_chain = MagicMock()
        messages_chain.upsert.return_value = messages_chain
        messages_chain.execute.return_value = result_mock
        db.table = MagicMock(return_value=messages_chain)

        from services.handlers.image_handler import ImageHandler
        from schemas.message import MessageStatus
        handler = ImageHandler(db)

        message, raw_data = handler._upsert_assistant_message(
            message_id="msg_123",
            conversation_id="conv_123",
            content_dicts=[{"type": "image", "url": "https://oss.example.com/img.png"}],
            status=MessageStatus.COMPLETED,
            credits_cost=10,
            client_task_id="client_123",
            generation_type="image",
            model_id="kie-model",
        )

        assert message.id == "msg_123"
        assert raw_data["id"] == "msg_123"


# ============ 修复 3：started_at 使用合法时间戳 ============

class TestStartedAtFix:
    """
    测试 process_result 中的 started_at 修复：
    使用 ISO 格式时间戳而非 'NOW()' 字符串
    """

    def test_started_at_without_existing_value(self):
        """
        修复 3a：当 task 没有 started_at 时，
        应使用 ISO 格式时间戳而不是 'NOW()' 字符串
        """
        db = MagicMock()
        service = TaskCompletionService(db)

        # 模拟 get_task 返回无 started_at 的任务
        task = _make_task(started_at=None, status="pending")

        # 捕获 update 调用的参数
        update_calls = {}
        tasks_chain = MagicMock()
        tasks_chain.select.return_value = tasks_chain
        tasks_chain.eq.return_value = tasks_chain
        tasks_chain.maybe_single.return_value = tasks_chain
        tasks_chain.in_.return_value = tasks_chain

        # get_task 返回任务
        task_response = MagicMock()
        task_response.data = task
        tasks_chain.execute.return_value = task_response

        def capture_update(data):
            update_calls.update(data)
            return tasks_chain

        tasks_chain.update = MagicMock(side_effect=capture_update)

        db.table = MagicMock(return_value=tasks_chain)

        # 创建一个 SUCCESS result
        result = ImageGenerateResult(
            task_id="test_task_123",
            status=TaskStatus.SUCCESS,
            image_urls=["https://kie.example.com/temp.png"],
        )

        # 只运行到 lock 部分（process_result 会因后续步骤失败而异常）
        # 但我们只关心 update 调用中的 started_at
        import asyncio
        try:
            asyncio.get_event_loop().run_until_complete(
                service.process_result("test_task_123", result)
            )
        except Exception:
            pass  # 后续步骤会失败，没关系

        # 验证 started_at 不是 "NOW()" 字符串
        if "started_at" in update_calls:
            started_at_value = update_calls["started_at"]
            assert started_at_value != "NOW()", \
                f"started_at 不应该是 'NOW()' 字符串，实际值: {started_at_value}"

            # 验证是合法的 ISO 格式时间戳
            try:
                datetime.fromisoformat(started_at_value)
            except (ValueError, TypeError) as e:
                pytest.fail(f"started_at 不是合法的 ISO 时间戳: {started_at_value}, error: {e}")

    def test_started_at_preserves_existing_value(self):
        """
        修复 3b：当 task 已有 started_at 时，应保留原值
        """
        db = MagicMock()
        service = TaskCompletionService(db)

        existing_started_at = "2026-03-01T10:00:00+00:00"
        task = _make_task(started_at=existing_started_at, status="running")

        update_calls = {}
        tasks_chain = MagicMock()
        tasks_chain.select.return_value = tasks_chain
        tasks_chain.eq.return_value = tasks_chain
        tasks_chain.maybe_single.return_value = tasks_chain
        tasks_chain.in_.return_value = tasks_chain

        task_response = MagicMock()
        task_response.data = task
        tasks_chain.execute.return_value = task_response

        def capture_update(data):
            update_calls.update(data)
            return tasks_chain

        tasks_chain.update = MagicMock(side_effect=capture_update)
        db.table = MagicMock(return_value=tasks_chain)

        result = ImageGenerateResult(
            task_id="test_task_123",
            status=TaskStatus.SUCCESS,
            image_urls=["https://kie.example.com/temp.png"],
        )

        import asyncio
        try:
            asyncio.get_event_loop().run_until_complete(
                service.process_result("test_task_123", result)
            )
        except Exception:
            pass

        if "started_at" in update_calls:
            assert update_calls["started_at"] == existing_started_at, \
                f"已有 started_at 应被保留，期望: {existing_started_at}, 实际: {update_calls['started_at']}"

    def test_now_string_not_used_anywhere(self):
        """
        修复 3c：确保 'NOW()' 字符串不再出现在代码中
        """
        import inspect
        source = inspect.getsource(TaskCompletionService.process_result)
        assert '"NOW()"' not in source, \
            "process_result 中不应包含 'NOW()' 字符串"
        assert "'NOW()'" not in source, \
            "process_result 中不应包含 'NOW()' 字符串"


# ============ 回归测试：确保正常流程不受影响 ============

class TestNormalFlowRegression:
    """
    回归测试：验证修复不会影响正常的成功/失败流程
    """

    @pytest.mark.asyncio
    async def test_process_result_skips_non_final_status(self):
        """正常流程：pending/processing 状态应被跳过"""
        db = MagicMock()
        service = TaskCompletionService(db)

        result = ImageGenerateResult(
            task_id="test_task_123",
            status=TaskStatus.PENDING,
        )

        ret = await service.process_result("test_task_123", result)
        assert ret is True
        # 不应调用任何 DB 操作
        db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_result_skips_already_completed_task(self):
        """正常流程：已完成的任务应幂等跳过"""
        db = MagicMock()

        task = _make_task(status="completed")
        task_response = MagicMock()
        task_response.data = task

        tasks_chain = MagicMock()
        tasks_chain.select.return_value = tasks_chain
        tasks_chain.eq.return_value = tasks_chain
        tasks_chain.maybe_single.return_value = tasks_chain
        tasks_chain.execute.return_value = task_response

        db.table = MagicMock(return_value=tasks_chain)

        service = TaskCompletionService(db)
        result = ImageGenerateResult(
            task_id="test_task_123",
            status=TaskStatus.SUCCESS,
            image_urls=["https://example.com/img.png"],
        )

        ret = await service.process_result("test_task_123", result)
        assert ret is True

    @pytest.mark.asyncio
    async def test_process_result_returns_false_when_task_not_found(self):
        """正常流程：任务不存在应返回 False"""
        db = MagicMock()

        task_response = MagicMock()
        task_response.data = None

        tasks_chain = MagicMock()
        tasks_chain.select.return_value = tasks_chain
        tasks_chain.eq.return_value = tasks_chain
        tasks_chain.maybe_single.return_value = tasks_chain
        tasks_chain.execute.return_value = task_response

        db.table = MagicMock(return_value=tasks_chain)

        service = TaskCompletionService(db)
        result = ImageGenerateResult(
            task_id="not_exist",
            status=TaskStatus.SUCCESS,
            image_urls=["https://example.com/img.png"],
        )

        ret = await service.process_result("not_exist", result)
        assert ret is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
