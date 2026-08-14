"""
services/handlers/chat_tool_mixin.py 单元测试

覆盖：_partition_tool_calls()、_execute_single_tool()、_accumulate_tool_call_delta()
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.handlers.chat_tool_mixin import accumulate_tool_call_delta
pytest_plugins = ("tests.tool_confirmation_fixtures",)


# ============================================================
# _partition_tool_calls 分批逻辑
# ============================================================


class TestPartitionToolCalls:
    """_partition_tool_calls() 按并发安全性分批"""

    def test_all_safe_tools_single_batch(self):
        """全部只读工具→合并为一批并行"""
        from services.handlers.chat_tool_mixin import _partition_tool_calls
        calls = [
            {"name": "local_stock_query", "id": "tc1"},
            {"name": "erp_product_query", "id": "tc2"},
            {"name": "erp_api_search", "id": "tc3"},
        ]
        batches = _partition_tool_calls(calls)
        assert len(batches) == 1
        is_safe, batch = batches[0]
        assert is_safe is True
        assert len(batch) == 3

    def test_all_unsafe_tools_separate_batches(self):
        """全部写操作→每个单独一批串行"""
        from services.handlers.chat_tool_mixin import _partition_tool_calls
        calls = [
            {"name": "erp_execute", "id": "tc1"},
            {"name": "trigger_erp_sync", "id": "tc2"},
        ]
        batches = _partition_tool_calls(calls)
        # 两个都是 unsafe，各自一批
        assert len(batches) == 2
        for is_safe, batch in batches:
            assert is_safe is False
            assert len(batch) == 1

    def test_mixed_safe_unsafe(self):
        """混合→安全的合批，不安全的单独"""
        from services.handlers.chat_tool_mixin import _partition_tool_calls
        calls = [
            {"name": "local_stock_query", "id": "tc1"},
            {"name": "erp_product_query", "id": "tc2"},
            {"name": "erp_execute", "id": "tc3"},
            {"name": "local_data", "id": "tc4"},
        ]
        batches = _partition_tool_calls(calls)
        assert len(batches) == 3
        # 第一批：2个安全工具
        assert batches[0][0] is True
        assert len(batches[0][1]) == 2
        # 第二批：1个不安全工具
        assert batches[1][0] is False
        assert len(batches[1][1]) == 1
        # 第三批：1个安全工具
        assert batches[2][0] is True
        assert len(batches[2][1]) == 1

    def test_empty_list(self):
        """空列表→空结果"""
        from services.handlers.chat_tool_mixin import _partition_tool_calls
        assert _partition_tool_calls([]) == []

    def test_single_safe_tool(self):
        """单个安全工具"""
        from services.handlers.chat_tool_mixin import _partition_tool_calls
        batches = _partition_tool_calls([{"name": "search_knowledge", "id": "tc1"}])
        assert len(batches) == 1
        assert batches[0][0] is True

    def test_single_unsafe_tool(self):
        """单个不安全工具"""
        from services.handlers.chat_tool_mixin import _partition_tool_calls
        batches = _partition_tool_calls([{"name": "erp_execute", "id": "tc1"}])
        assert len(batches) == 1
        assert batches[0][0] is False


# ============================================================
# _execute_single_tool 安全检查 + 执行
# ============================================================


def _make_mixin():
    """构造一个 mock ChatToolMixin 实例"""
    from services.handlers.chat_tool_mixin import ChatToolMixin
    mixin = MagicMock()
    mixin.db = MagicMock()
    mixin.org_id = None
    # 绑定真实方法(_extract_file_parts 已删 — 沙盒 IO 统一协议)
    mixin._push_tool_step_update = ChatToolMixin._push_tool_step_update.__get__(mixin)
    return mixin


class TestExecuteSingleTool:
    """_execute_single_tool() 安全检查 + 执行"""

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_dangerous_tool_rejected(self, mock_ws, mock_v3_confirmation):
        """dangerous 工具→用户拒绝→不执行，返回拒绝提示"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        from services.tool_confirmation.types import ConfirmationDecision, ConfirmationOutcome
        mock_v3_confirmation.await_and_claim.return_value = ConfirmationDecision(
            ConfirmationOutcome.DENIED, "TERMINAL_DENIED",
        )
        executor = AsyncMock()

        tc = {"name": "erp_execute", "id": "tc1", "arguments": '{"action":"cancel"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
        )
        tc_out, text, is_error, _display = result
        assert is_error is True
        assert "Agent Runtime" in text
        # 不应该调用 executor
        executor.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_safe_tool_executes(self, mock_ws):
        """safe 工具→直接执行"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="库存100件")

        tc = {"name": "local_stock_query", "id": "tc1", "arguments": '{"product_code":"SKU001"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
        )
        tc_out, text, is_error, _display = result
        assert is_error is False
        assert "库存100件" in text
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_tool_execution_error_returns_error(self, mock_ws):
        """工具执行异常→返回错误，不中断"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(side_effect=Exception("API timeout"))

        tc = {"name": "erp_product_query", "id": "tc1", "arguments": '{"action":"product_list"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
        )
        tc_out, text, is_error, _display = result
        assert is_error is True
        assert "失败" in text

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_invalid_json_arguments(self, mock_ws):
        """无效 JSON 参数→返回解析错误"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()

        tc = {"name": "local_stock_query", "id": "tc1", "arguments": "not json{{{"}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
        )
        tc_out, text, is_error, _display = result
        assert is_error is True
        assert "参数解析失败" in text

# ============================================================
# _accumulate_tool_call_delta 增量累积
# ============================================================


class TestAccumulateToolCallDelta:
    """accumulate_tool_call_delta() 增量累积"""

    def test_single_complete_delta(self):
        """单个完整的 tool_call delta"""
        from services.adapters.types import ToolCallDelta

        acc = {}
        deltas = [ToolCallDelta(index=0, id="tc1", name="web_search", arguments_delta='{"query":"test"}')]
        accumulate_tool_call_delta(acc, deltas)

        assert 0 in acc
        assert acc[0]["id"] == "tc1"
        assert acc[0]["name"] == "web_search"
        assert acc[0]["arguments"] == '{"query":"test"}'

    def test_incremental_arguments(self):
        """arguments 增量拼接"""
        from services.adapters.types import ToolCallDelta

        acc = {}
        # 第一帧：id + name + 部分 arguments
        accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id="tc1", name="erp_query", arguments_delta='{"action":'),
        ])
        # 第二帧：只有 arguments 增量
        accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, arguments_delta='"order_list"}'),
        ])

        assert acc[0]["id"] == "tc1"
        assert acc[0]["name"] == "erp_query"
        assert acc[0]["arguments"] == '{"action":"order_list"}'

    def test_multiple_tools(self):
        """多个工具同时累积"""
        from services.adapters.types import ToolCallDelta

        acc = {}
        accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id="tc1", name="local_stock_query", arguments_delta='{"code":"A"}'),
            ToolCallDelta(index=1, id="tc2", name="local_data", arguments_delta='{"code":"B"}'),
        ])

        assert len(acc) == 2
        assert acc[0]["name"] == "local_stock_query"
        assert acc[1]["name"] == "local_data"

    def test_empty_deltas(self):
        """空 deltas 列表→acc 不变"""
        acc = {}
        accumulate_tool_call_delta(acc, [])
        assert len(acc) == 0

    def test_none_fields_ignored(self):
        """None 字段不覆盖已有值"""
        from services.adapters.types import ToolCallDelta

        acc = {}
        accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id="tc1", name="test"),
        ])
        accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id=None, name=None, arguments_delta="args"),
        ])

        assert acc[0]["id"] == "tc1"
        assert acc[0]["name"] == "test"
        assert acc[0]["arguments"] == "args"




# ============================================================
# AgentResult 处理（通信协议 §3.2）
# ============================================================


class TestExecuteSingleToolAgentResult:
    """_execute_single_tool 收到 AgentResult 时的短路路径"""

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_agent_result_returned_directly(self, mock_ws):
        """AgentResult 不经过 _extract_file_parts / wrap，直接返回"""
        from services.agent.agent_result import AgentResult
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=AgentResult(
            status="success", summary="共 945 条订单",
            source="erp_agent", tokens_used=500,
        ))

        tc = {"name": "search_knowledge", "id": "tc1", "arguments": '{"query":"查订单"}'}
        tc_out, result, is_error, _display = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        assert isinstance(result, AgentResult)
        assert result.summary == "共 945 条订单"
        assert is_error is False

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_agent_result_error_status(self, mock_ws):
        """AgentResult status=error → is_error=True"""
        from services.agent.agent_result import AgentResult
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=AgentResult(
            status="error", summary="查询超时",
            source="erp_agent", error_message="查询超时",
        ))

        tc = {"name": "search_knowledge", "id": "tc1", "arguments": '{"query":"查订单"}'}
        tc_out, result, is_error, _display = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        assert isinstance(result, AgentResult)
        assert is_error is True

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_agent_result_sends_ws_notification(self, mock_ws):
        """AgentResult 仍发 ws build_tool_result 通知前端"""
        from services.agent.agent_result import AgentResult
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=AgentResult(
            status="success", summary="ok",
            source="erp_agent",
        ))

        tc = {"name": "search_knowledge", "id": "tc1", "arguments": '{"query":"查"}'}
        await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        mock_ws.send_tool_confirmation.assert_not_awaited()
        assert mock_ws.send_to_task_or_user.call_count == 2

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_str_result_still_works(self, mock_ws):
        """普通 str 返回不受影响（旧路径兼容）"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.send_tool_confirmation = AsyncMock(return_value=True)
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="搜索结果：3条")

        tc = {"name": "search_knowledge", "id": "tc1", "arguments": '{"query":"天气"}'}
        tc_out, result, is_error, _display = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        assert isinstance(result, str)
        assert is_error is False


class TestExecuteToolCallsAgentResult:
    """_execute_tool_calls 的 AgentResult 处理循环"""

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_emit_payloads_to_pending(self, mock_ws):
        """AgentResult.emit_payloads → self._pending_emit_payloads(沙盒 IO 统一协议)"""
        from services.agent.agent_result import AgentResult

        mixin = _make_mixin()
        mixin._pending_emit_payloads = []
        mixin._last_erp_display_text = None
        mixin._last_erp_display_files = []
        mixin._erp_agent_tokens = 0
        mock_ws.send_to_task_or_user = AsyncMock()

        payloads = [{
            "kind": "file", "url": "/tmp/a.parquet", "name": "a.parquet",
            "mime_type": "application/octet-stream", "size": 1024,
        }]
        agent_result = AgentResult(
            status="success", summary="已导出",
            emit_payloads=payloads, source="erp_agent", tokens_used=300,
        )

        # 模拟 _execute_tool_calls 中的 emit_payloads 聚合逻辑
        results = [
            ({"name": "erp_agent", "id": "tc1"}, agent_result, False, ""),
        ]
        for tc, result, _is_error, _display in results:
            if isinstance(result, AgentResult):
                if result.emit_payloads:
                    mixin._pending_emit_payloads.extend(result.emit_payloads)
                mixin._erp_agent_tokens += result.tokens_used

        assert len(mixin._pending_emit_payloads) == 1
        assert mixin._pending_emit_payloads[0]["url"] == "/tmp/a.parquet"
        assert mixin._pending_emit_payloads[0]["kind"] == "file"
        assert mixin._erp_agent_tokens == 300
