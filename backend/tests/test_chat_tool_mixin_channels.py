"""Chat tool form-block and tool-step channel contracts."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mixin():
    from services.handlers.chat_tool_mixin import ChatToolMixin

    mixin = MagicMock()
    mixin.db = MagicMock()
    mixin.org_id = None
    mixin._push_tool_step_update = ChatToolMixin._push_tool_step_update.__get__(mixin)
    return mixin


class TestFormBlockResultChannel:
    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_form_block_stores_pending_and_returns_hint(self, mock_ws):
        from services.handlers.chat_tool_mixin import ChatToolMixin
        from services.scheduler.chat_task_manager import FormBlockResult

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=FormBlockResult(
            form={
                "type": "form",
                "form_type": "scheduled_task_create",
                "form_id": "test_form_1",
                "title": "创建定时任务",
                "fields": [],
            },
            llm_hint="已向用户展示创建定时任务，等待用户确认。",
        ))
        tool_call = {
            "name": "manage_scheduled_task",
            "id": "tc1",
            "arguments": '{"action":"create","description":"每天9点推日报"}',
        }

        _, result, is_error, _ = await ChatToolMixin._execute_single_tool(
            mixin, tool_call, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        assert is_error is True
        assert isinstance(result, str)
        assert "Agent Runtime" in result
        mock_ws.send_tool_confirmation.assert_not_awaited()
        executor.execute.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_form_block_emits_audit(self, mock_ws):
        from services.handlers.chat_tool_mixin import ChatToolMixin
        from services.scheduler.chat_task_manager import FormBlockResult

        mixin = _make_mixin()
        mixin._emit_tool_audit = MagicMock()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=FormBlockResult(
            form={"type": "form", "form_type": "scheduled_task_update", "fields": []},
        ))
        tool_call = {
            "name": "manage_scheduled_task",
            "id": "tc2",
            "arguments": '{"action":"update","task_name":"日报"}',
        }

        await ChatToolMixin._execute_single_tool(
            mixin, tool_call, executor, "task1", "conv1", "msg1", "user1", 2,
        )

        mixin._emit_tool_audit.assert_not_called()
        executor.execute.assert_not_awaited()


class TestPushToolStepUpdate:
    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_completed_step_pushes_content_block_add(self, mock_ws):
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mixin.org_id = "org-1"
        mock_ws.send_to_task_or_user = AsyncMock()

        await ChatToolMixin._push_tool_step_update(
            mixin, "task1", "conv1", "msg1", "user1", "web_search", "tc_1",
            success=True, output="找到3条结果", elapsed_ms=1500,
        )

        mock_ws.send_to_task_or_user.assert_called_once()
        assert mock_ws.send_to_task_or_user.call_args.kwargs["org_id"] == "org-1"
        block = mock_ws.send_to_task_or_user.call_args[0][2]["payload"]["block"]
        assert block["type"] == "tool_step"
        assert block["tool_call_id"] == "tc_1"
        assert block["status"] == "completed"
        assert block["output"] == "找到3条结果"
        assert block["elapsed_ms"] == 1500

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_error_step_pushes_error_status(self, mock_ws):
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        await ChatToolMixin._push_tool_step_update(
            mixin, "task1", "conv1", "msg1", "user1", "erp_agent", "tc_2",
            success=False, output="连接超时", elapsed_ms=30000,
        )
        block = mock_ws.send_to_task_or_user.call_args[0][2]["payload"]["block"]
        assert block["status"] == "error"
        assert block["output"] == "连接超时"

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_code_execute_includes_output(self, mock_ws):
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        await ChatToolMixin._push_tool_step_update(
            mixin, "task1", "conv1", "msg1", "user1", "code_execute", "tc_3",
            success=True,
            output="图表已生成\n处理了120条数据\n图表已保存",
            elapsed_ms=5000,
        )
        block = mock_ws.send_to_task_or_user.call_args[0][2]["payload"]["block"]
        assert "图表已生成" in block["output"]

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_ws_failure_does_not_raise(self, mock_ws):
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock(side_effect=ConnectionError("WS断开"))
        await ChatToolMixin._push_tool_step_update(
            mixin, "task1", "conv1", "msg1", "user1", "web_search", "tc_4",
            success=True, output="ok", elapsed_ms=100,
        )

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_empty_output_is_preserved(self, mock_ws):
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        await ChatToolMixin._push_tool_step_update(
            mixin, "task1", "conv1", "msg1", "user1", "web_search", "tc_5",
            success=True, output="", elapsed_ms=100,
        )
        block = mock_ws.send_to_task_or_user.call_args[0][2]["payload"]["block"]
        assert block["output"] == ""
