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
            {"name": "local_order_query", "id": "tc4"},
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
        batches = _partition_tool_calls([{"name": "web_search", "id": "tc1"}])
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
    mixin = MagicMock()
    mixin.db = MagicMock()
    mixin.org_id = None
    return mixin


class TestExecuteSingleTool:
    """_execute_single_tool() 安全检查 + 执行"""

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_dangerous_tool_rejected(self, mock_ws):
        """dangerous 工具→拒绝执行，返回提示"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_subscribers = AsyncMock()
        executor = AsyncMock()

        tc = {"name": "erp_execute", "id": "tc1", "arguments": '{"action":"cancel"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", 1,
        )
        tc_out, text, is_error = result
        assert is_error is True
        assert "用户确认" in text
        # 不应该调用 executor
        executor.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_safe_tool_executes(self, mock_ws):
        """safe 工具→直接执行"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_subscribers = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="库存100件")

        tc = {"name": "local_stock_query", "id": "tc1", "arguments": '{"product_code":"SKU001"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", 1,
        )
        tc_out, text, is_error = result
        assert is_error is False
        assert "库存100件" in text
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_tool_execution_error_returns_error(self, mock_ws):
        """工具执行异常→返回错误，不中断"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_subscribers = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(side_effect=Exception("API timeout"))

        tc = {"name": "erp_product_query", "id": "tc1", "arguments": '{"action":"product_list"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", 1,
        )
        tc_out, text, is_error = result
        assert is_error is True
        assert "失败" in text

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_invalid_json_arguments(self, mock_ws):
        """无效 JSON 参数→返回解析错误"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_subscribers = AsyncMock()
        executor = AsyncMock()

        tc = {"name": "local_stock_query", "id": "tc1", "arguments": "not json{{{"}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", 1,
        )
        tc_out, text, is_error = result
        assert is_error is True
        assert "参数解析失败" in text

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_confirm_tool_executes_with_log(self, mock_ws):
        """confirm 工具→正常执行（通知但不阻塞）"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_subscribers = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="图片生成中")

        tc = {"name": "generate_image", "id": "tc1", "arguments": '{"prompt":"cat"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", 1,
        )
        tc_out, text, is_error = result
        assert is_error is False
        executor.execute.assert_called_once()


# ============================================================
# _accumulate_tool_call_delta 增量累积
# ============================================================


class TestAccumulateToolCallDelta:
    """ChatHandler._accumulate_tool_call_delta() 增量累积"""

    def test_single_complete_delta(self):
        """单个完整的 tool_call delta"""
        from services.handlers.chat_handler import ChatHandler
        from services.adapters.types import ToolCallDelta

        acc = {}
        deltas = [ToolCallDelta(index=0, id="tc1", name="web_search", arguments_delta='{"query":"test"}')]
        ChatHandler._accumulate_tool_call_delta(acc, deltas)

        assert 0 in acc
        assert acc[0]["id"] == "tc1"
        assert acc[0]["name"] == "web_search"
        assert acc[0]["arguments"] == '{"query":"test"}'

    def test_incremental_arguments(self):
        """arguments 增量拼接"""
        from services.handlers.chat_handler import ChatHandler
        from services.adapters.types import ToolCallDelta

        acc = {}
        # 第一帧：id + name + 部分 arguments
        ChatHandler._accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id="tc1", name="erp_query", arguments_delta='{"action":'),
        ])
        # 第二帧：只有 arguments 增量
        ChatHandler._accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, arguments_delta='"order_list"}'),
        ])

        assert acc[0]["id"] == "tc1"
        assert acc[0]["name"] == "erp_query"
        assert acc[0]["arguments"] == '{"action":"order_list"}'

    def test_multiple_tools(self):
        """多个工具同时累积"""
        from services.handlers.chat_handler import ChatHandler
        from services.adapters.types import ToolCallDelta

        acc = {}
        ChatHandler._accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id="tc1", name="local_stock_query", arguments_delta='{"code":"A"}'),
            ToolCallDelta(index=1, id="tc2", name="local_order_query", arguments_delta='{"code":"B"}'),
        ])

        assert len(acc) == 2
        assert acc[0]["name"] == "local_stock_query"
        assert acc[1]["name"] == "local_order_query"

    def test_empty_deltas(self):
        """空 deltas 列表→acc 不变"""
        from services.handlers.chat_handler import ChatHandler

        acc = {}
        ChatHandler._accumulate_tool_call_delta(acc, [])
        assert len(acc) == 0

    def test_none_fields_ignored(self):
        """None 字段不覆盖已有值"""
        from services.handlers.chat_handler import ChatHandler
        from services.adapters.types import ToolCallDelta

        acc = {}
        ChatHandler._accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id="tc1", name="test"),
        ])
        ChatHandler._accumulate_tool_call_delta(acc, [
            ToolCallDelta(index=0, id=None, name=None, arguments_delta="args"),
        ])

        assert acc[0]["id"] == "tc1"
        assert acc[0]["name"] == "test"
        assert acc[0]["arguments"] == "args"
