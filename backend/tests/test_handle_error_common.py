"""
_handle_error_common 容错性测试

验证错误处理路径中每一步失败都不会导致崩溃，
且任务最终进入终态。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from schemas.message import GenerationType


# ============================================================
# 辅助：构造一个实现了所有 mixin 的 handler
# ============================================================

def _make_handler():
    """构造最小化 handler，用于测试 _handle_error_common"""
    from services.handlers.chat_handler import ChatHandler
    handler = ChatHandler(db=MagicMock())
    return handler


def _mock_task(task_id="t1"):
    """返回一个标准 task dict"""
    return {
        "external_task_id": task_id,
        "placeholder_message_id": "msg_1",
        "conversation_id": "conv_1",
        "model_id": "test-model",
        "client_task_id": "client_1",
        "user_id": "user_1",
        "status": "running",
        "version": 1,
        "org_id": None,
        "request_params": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# Tests
# ============================================================


class TestHandleErrorCommon:

    @pytest.mark.asyncio
    async def test_normal_error_flow(self):
        """正常错误流程：退积分 + upsert 消息 + WS + fail_task"""
        handler = _make_handler()
        task = _mock_task()

        handler._get_task_context = MagicMock(return_value=task)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._handle_credits_on_error = AsyncMock()
        handler._upsert_assistant_message = MagicMock(return_value=(MagicMock(), {}))
        handler._push_ws_message = AsyncMock()
        handler._fail_task = MagicMock()

        result = await handler._handle_error_common("t1", "TEST_ERROR", "something failed")

        handler._handle_credits_on_error.assert_awaited_once()
        handler._upsert_assistant_message.assert_called_once()
        handler._push_ws_message.assert_awaited_once()
        handler._fail_task.assert_called_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_upsert_fails_task_still_marked_failed(self):
        """消息 upsert 失败 → 任务仍被标记为 failed"""
        handler = _make_handler()
        task = _mock_task()

        handler._get_task_context = MagicMock(return_value=task)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._handle_credits_on_error = AsyncMock()
        handler._upsert_assistant_message = MagicMock(
            side_effect=Exception("DB upsert failed")
        )
        handler._push_ws_message = AsyncMock()
        handler._fail_task = MagicMock()

        # 不崩溃
        result = await handler._handle_error_common("t1", "ERR", "test")

        # upsert 失败，但 _fail_task 仍被调用
        handler._fail_task.assert_called_once()
        # message 返回 None（upsert 失败）
        assert result is None

    @pytest.mark.asyncio
    async def test_ws_push_fails_task_still_marked_failed(self):
        """WS 推送失败 → 任务仍被标记为 failed"""
        handler = _make_handler()
        task = _mock_task()

        handler._get_task_context = MagicMock(return_value=task)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._handle_credits_on_error = AsyncMock()
        handler._upsert_assistant_message = MagicMock(return_value=(MagicMock(), {}))
        handler._push_ws_message = AsyncMock(
            side_effect=Exception("WS connection closed")
        )
        handler._fail_task = MagicMock()

        result = await handler._handle_error_common("t1", "ERR", "test")

        handler._fail_task.assert_called_once()
        assert result is not None  # message was upserted

    @pytest.mark.asyncio
    async def test_credit_refund_fails_continues(self):
        """积分退回失败 → 继续后续流程"""
        handler = _make_handler()
        task = _mock_task()

        handler._get_task_context = MagicMock(return_value=task)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._handle_credits_on_error = AsyncMock(
            side_effect=Exception("refund RPC failed")
        )
        handler._upsert_assistant_message = MagicMock(return_value=(MagicMock(), {}))
        handler._push_ws_message = AsyncMock()
        handler._fail_task = MagicMock()

        # 不崩溃
        result = await handler._handle_error_common("t1", "ERR", "test")

        # 后续流程仍然执行
        handler._upsert_assistant_message.assert_called_once()
        handler._fail_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_fail_task_fails_no_crash(self):
        """_fail_task 本身失败 → 不崩溃"""
        handler = _make_handler()
        task = _mock_task()

        handler._get_task_context = MagicMock(return_value=task)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._handle_credits_on_error = AsyncMock()
        handler._upsert_assistant_message = MagicMock(return_value=(MagicMock(), {}))
        handler._push_ws_message = AsyncMock()
        handler._fail_task = MagicMock(
            side_effect=Exception("task update DB error")
        )

        # 不崩溃
        result = await handler._handle_error_common("t1", "ERR", "test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_all_steps_fail_no_crash(self):
        """所有步骤都失败 → 仍然不崩溃"""
        handler = _make_handler()
        task = _mock_task()

        handler._get_task_context = MagicMock(return_value=task)
        handler._check_idempotency = MagicMock(return_value=None)
        handler._handle_credits_on_error = AsyncMock(side_effect=Exception("refund fail"))
        handler._upsert_assistant_message = MagicMock(side_effect=Exception("upsert fail"))
        handler._push_ws_message = AsyncMock(side_effect=Exception("ws fail"))
        handler._fail_task = MagicMock(side_effect=Exception("fail_task fail"))

        # 绝对不崩溃
        result = await handler._handle_error_common("t1", "ERR", "test")
        assert result is None  # upsert 失败，message 为 None

    @pytest.mark.asyncio
    async def test_idempotency_returns_existing(self):
        """幂等检查命中 → 直接返回已有消息"""
        handler = _make_handler()
        task = _mock_task()
        task["status"] = "failed"
        existing_msg = MagicMock()

        handler._get_task_context = MagicMock(return_value=task)
        handler._check_idempotency = MagicMock(return_value=existing_msg)
        handler._handle_credits_on_error = AsyncMock()

        result = await handler._handle_error_common("t1", "ERR", "test")

        assert result is existing_msg
        handler._handle_credits_on_error.assert_not_awaited()
