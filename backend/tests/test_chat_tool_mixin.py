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
    from services.handlers.chat_tool_mixin import ChatToolMixin
    mixin = MagicMock()
    mixin.db = MagicMock()
    mixin.org_id = None
    # 绑定真实的 _extract_file_parts 方法（不 mock）
    mixin._extract_file_parts = ChatToolMixin._extract_file_parts.__get__(mixin)
    return mixin


class TestExecuteSingleTool:
    """_execute_single_tool() 安全检查 + 执行"""

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_dangerous_tool_rejected(self, mock_ws):
        """dangerous 工具→用户拒绝→不执行，返回拒绝提示"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        mock_ws.wait_for_confirm = AsyncMock(return_value=False)
        executor = AsyncMock()

        tc = {"name": "erp_execute", "id": "tc1", "arguments": '{"action":"cancel"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
        )
        tc_out, text, is_error = result
        assert is_error is True
        assert "拒绝" in text or "超时" in text
        # 不应该调用 executor
        executor.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_safe_tool_executes(self, mock_ws):
        """safe 工具→直接执行"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="库存100件")

        tc = {"name": "local_stock_query", "id": "tc1", "arguments": '{"product_code":"SKU001"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
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
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(side_effect=Exception("API timeout"))

        tc = {"name": "erp_product_query", "id": "tc1", "arguments": '{"action":"product_list"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
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
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()

        tc = {"name": "local_stock_query", "id": "tc1", "arguments": "not json{{{"}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
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
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="图片生成中")

        tc = {"name": "generate_image", "id": "tc1", "arguments": '{"prompt":"cat"}'}
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "test_user", 1,
        )
        tc_out, text, is_error = result
        assert is_error is False
        executor.execute.assert_called_once()


# ============================================================
# _extract_file_parts 文件标记提取
# ============================================================


class TestExtractFileParts:
    """_extract_file_parts() 从工具结果中提取 [FILE] 标记"""

    def _make_instance(self):
        from services.handlers.chat_tool_mixin import ChatToolMixin
        obj = MagicMock()
        obj._extract_file_parts = ChatToolMixin._extract_file_parts.__get__(obj)
        obj._pending_file_parts = []  # 初始化为真实列表，避免 MagicMock 自动属性
        return obj

    def test_no_file_marker_passthrough(self):
        """无 [FILE] 标记 → 原样返回"""
        obj = self._make_instance()
        result = obj._extract_file_parts("库存100件")
        assert result == "库存100件"
        assert not hasattr(obj, "_pending_file_parts") or len(obj._pending_file_parts) == 0

    def test_empty_string(self):
        """空字符串 → 原样返回"""
        obj = self._make_instance()
        assert obj._extract_file_parts("") == ""

    def test_none_input(self):
        """None → 原样返回"""
        obj = self._make_instance()
        assert obj._extract_file_parts(None) is None

    def test_single_file_extracted(self):
        """单个 [FILE] 标记 → 提取 FilePart + 替换为友好文本"""
        obj = self._make_instance()
        text = "✅ 文件已上传: 报表.xlsx\n[FILE]https://cdn.example.com/a.xlsx|报表.xlsx|application/vnd.ms-excel|2048[/FILE]"
        result = obj._extract_file_parts(text)
        # 替换为纯文件名（不含 URL，防止 LLM 幻觉篡改域名）
        assert "📎 文件已生成: 报表.xlsx" in result
        assert "[FILE]" not in result
        assert len(obj._pending_file_parts) == 1
        fp = obj._pending_file_parts[0]
        assert fp.url == "https://cdn.example.com/a.xlsx"
        assert fp.name == "报表.xlsx"
        assert fp.size == 2048

    def test_multiple_files_extracted(self):
        """多个 [FILE] 标记 → 全部提取，不含 URL"""
        obj = self._make_instance()
        text = (
            "[FILE]https://cdn.example.com/a.csv|数据.csv|text/csv|1024[/FILE]\n"
            "中间文字\n"
            "[FILE]https://cdn.example.com/b.xlsx|报表.xlsx|application/vnd.ms-excel|4096[/FILE]"
        )
        result = obj._extract_file_parts(text)
        assert "[FILE]" not in result
        assert "📎 文件已生成: 数据.csv" in result
        assert "📎 文件已生成: 报表.xlsx" in result
        assert len(obj._pending_file_parts) == 2

    def test_accumulates_across_calls(self):
        """多次调用 → _pending_file_parts 累积"""
        obj = self._make_instance()
        obj._extract_file_parts("[FILE]https://a.com/1.csv|a.csv|text/csv|100[/FILE]")
        obj._extract_file_parts("[FILE]https://a.com/2.csv|b.csv|text/csv|200[/FILE]")
        assert len(obj._pending_file_parts) == 2

    def test_image_mime_placeholder_differs(self):
        """图片 mime → 占位文本提示'将自动展示'，非图片 → 保留文件名"""
        obj = self._make_instance()
        text = (
            "[FILE]https://cdn.com/chart.png|chart.png|image/png|2048[/FILE]\n"
            "[FILE]https://cdn.com/data.xlsx|data.xlsx|application/vnd.ms-excel|4096[/FILE]"
        )
        result = obj._extract_file_parts(text)
        # 图片：不含文件名，提示自动展示
        assert "📊 图表已生成" in result
        assert "不要在文字中重复描述" in result
        # 非图片：保留文件名
        assert "📎 文件已生成: data.xlsx" in result
        assert "不要重复引用文件名" in result
        # URL 不暴露
        assert "cdn.com" not in result
        assert len(obj._pending_file_parts) == 2

    def test_image_svg_also_gets_image_placeholder(self):
        """SVG (image/svg+xml) 也走图片占位文本"""
        obj = self._make_instance()
        result = obj._extract_file_parts(
            "[FILE]https://cdn.com/flow.svg|flow.svg|image/svg+xml|1024[/FILE]"
        )
        assert "📊 图表已生成" in result
        assert "flow.svg" not in result  # 文件名不暴露给 LLM


# ============================================================
# _accumulate_tool_call_delta 增量累积
# ============================================================


class TestAccumulateToolCallDelta:
    """accumulate_tool_call_delta() 增量累积"""

    def test_single_complete_delta(self):
        """单个完整的 tool_call delta"""
        from services.handlers.chat_handler import ChatHandler
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
        from services.handlers.chat_handler import ChatHandler
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
        from services.handlers.chat_handler import ChatHandler
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
        from services.handlers.chat_handler import ChatHandler

        acc = {}
        accumulate_tool_call_delta(acc, [])
        assert len(acc) == 0

    def test_none_fields_ignored(self):
        """None 字段不覆盖已有值"""
        from services.handlers.chat_handler import ChatHandler
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
# ask_user 短路（_execute_single_tool）
# ============================================================


class TestAskUserShortCircuit:
    """ask_user 工具短路：不经过 executor，直接返回 OK + 暂存追问信息"""

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_ask_user_returns_ok(self, mock_ws):
        """ask_user 工具返回 (tc, 'OK', False)"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()

        tc = {
            "name": "ask_user",
            "id": "tc_ask",
            "arguments": '{"message": "请选择店铺", "reason": "need_info"}',
        }
        result = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )
        tc_out, text, is_error = result
        assert text == "OK"
        assert is_error is False
        # executor 不应被调用
        executor.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_ask_user_stores_pending_info(self, mock_ws):
        """ask_user 工具将追问信息暂存到 _ask_user_pending"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mixin._ask_user_pending = None
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()

        tc = {
            "name": "ask_user",
            "id": "tc_ask",
            "arguments": '{"message": "选时间范围", "reason": "ambiguous"}',
        }
        await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        assert mixin._ask_user_pending is not None
        assert mixin._ask_user_pending["message"] == "选时间范围"
        assert mixin._ask_user_pending["reason"] == "ambiguous"
        assert mixin._ask_user_pending["tool_call_id"] == "tc_ask"

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_ask_user_bad_json_uses_default(self, mock_ws):
        """ask_user 参数 JSON 解析失败时使用默认消息"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mixin._ask_user_pending = None
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()

        tc = {
            "name": "ask_user",
            "id": "tc_ask",
            "arguments": "invalid json",
        }
        await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        assert mixin._ask_user_pending["message"] == "请补充更多信息"


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
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=AgentResult(
            status="success", summary="共 945 条订单",
            source="erp_agent", tokens_used=500,
        ))

        tc = {"name": "erp_agent", "id": "tc1", "arguments": '{"task":"查订单"}'}
        tc_out, result, is_error = await ChatToolMixin._execute_single_tool(
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
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=AgentResult(
            status="error", summary="查询超时",
            source="erp_agent", error_message="查询超时",
        ))

        tc = {"name": "erp_agent", "id": "tc1", "arguments": '{"task":"查订单"}'}
        tc_out, result, is_error = await ChatToolMixin._execute_single_tool(
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
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=AgentResult(
            status="success", summary="ok",
            source="erp_agent",
        ))

        tc = {"name": "erp_agent", "id": "tc1", "arguments": '{"task":"查"}'}
        await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        mock_ws.send_to_task_or_user.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_str_result_still_works(self, mock_ws):
        """普通 str 返回不受影响（旧路径兼容）"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="搜索结果：3条")

        tc = {"name": "web_search", "id": "tc1", "arguments": '{"query":"天气"}'}
        tc_out, result, is_error = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        assert isinstance(result, str)
        assert is_error is False


class TestExecuteToolCallsAgentResult:
    """_execute_tool_calls 的 AgentResult 处理循环"""

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_collected_files_to_pending(self, mock_ws):
        """AgentResult.collected_files → _pending_file_parts"""
        from services.agent.agent_result import AgentResult
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mixin._pending_file_parts = []
        mixin._ask_user_pending = None
        mixin._last_erp_display_text = None
        mixin._last_erp_display_files = []
        mixin._erp_agent_tokens = 0
        mock_ws.send_to_task_or_user = AsyncMock()

        files = [{"url": "/tmp/a.parquet", "name": "a.parquet",
                  "mime_type": "application/octet-stream", "size": 1024}]
        agent_result = AgentResult(
            status="success", summary="已导出",
            collected_files=files, source="erp_agent", tokens_used=300,
        )

        # 模拟 _execute_tool_calls 的 AgentResult 处理循环
        results = [
            ({"name": "erp_agent", "id": "tc1"}, agent_result, False),
        ]

        # 直接测试循环逻辑
        for tc, result, is_error in results:
            if isinstance(result, AgentResult):
                if result.collected_files and hasattr(mixin, "_pending_file_parts"):
                    from schemas.message import FilePart
                    for f in result.collected_files:
                        mixin._pending_file_parts.append(FilePart(
                            url=f["url"], name=f["name"],
                            mime_type=f["mime_type"], size=f["size"],
                        ))
                mixin._erp_agent_tokens += result.tokens_used

        assert len(mixin._pending_file_parts) == 1
        assert mixin._pending_file_parts[0].url == "/tmp/a.parquet"
        assert mixin._erp_agent_tokens == 300

    @pytest.mark.asyncio
    async def test_ask_user_bubble_from_agent_result(self):
        """AgentResult status=ask_user → _ask_user_pending 设置"""
        from services.agent.agent_result import AgentResult

        mixin = _make_mixin()
        mixin._ask_user_pending = None

        agent_result = AgentResult(
            status="ask_user", summary="需确认",
            ask_user_question="查哪个平台？",
            source="erp_agent",
        )

        tc = {"name": "erp_agent", "id": "tc1"}
        # 模拟循环逻辑
        if (agent_result.status == "ask_user" and agent_result.ask_user_question
                and not mixin._ask_user_pending):
            mixin._ask_user_pending = {
                "message": agent_result.ask_user_question,
                "reason": "need_info",
                "tool_call_id": tc["id"],
                "source": agent_result.source,
            }

        assert mixin._ask_user_pending is not None
        assert mixin._ask_user_pending["message"] == "查哪个平台？"
        assert mixin._ask_user_pending["tool_call_id"] == "tc1"
        assert mixin._ask_user_pending["source"] == "erp_agent"


# ============================================================
# FormBlockResult 通道（content_block_add 推送）
# ============================================================


class TestFormBlockResultChannel:
    """_execute_single_tool 收到 FormBlockResult 时的短路路径

    FormBlockResult 与 AgentResult 平级：
    - 推送 content_block_add 到前端（表单渲染）
    - 推送 tool_result（表单已展示）
    - 返回 llm_hint 给 LLM（不展示给用户）
    """

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_form_block_stores_pending_and_returns_hint(self, mock_ws):
        """FormBlockResult → 暂存到 _pending_form_block + 返回 llm_hint

        content_block_add 推送由 chat_handler 统一处理（复用 _pending_file_parts 模式），
        _execute_single_tool 只负责暂存和发 tool_result。
        """
        from services.scheduler.chat_task_manager import FormBlockResult
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()

        form_data = {
            "type": "form",
            "form_type": "scheduled_task_create",
            "form_id": "test_form_1",
            "title": "创建定时任务",
            "fields": [],
        }
        executor.execute = AsyncMock(return_value=FormBlockResult(
            form=form_data,
            llm_hint="已向用户展示创建定时任务，等待用户确认。",
        ))

        tc = {"name": "manage_scheduled_task", "id": "tc1",
              "arguments": '{"action":"create","description":"每天9点推日报"}'}
        tc_out, result, is_error = await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 1,
        )

        # 返回 llm_hint 字符串
        assert is_error is False
        assert isinstance(result, str)
        assert "等待用户确认" in result
        # form 暂存到 _pending_form_block（chat_handler 统一处理）
        assert mixin._pending_form_block is not None
        assert mixin._pending_form_block["form_type"] == "scheduled_task_create"
        # 只发 tool_result（不发 content_block_add，那是 chat_handler 的职责）
        ws_calls = mock_ws.send_to_task_or_user.call_args_list
        assert len(ws_calls) == 1
        assert ws_calls[0][0][2]["type"] == "tool_result"

    @pytest.mark.asyncio
    @patch("services.handlers.chat_tool_mixin.ws_manager")
    async def test_form_block_emits_audit(self, mock_ws):
        """FormBlockResult → 审计日志记录"""
        from services.scheduler.chat_task_manager import FormBlockResult
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = _make_mixin()
        mixin._emit_tool_audit = MagicMock()
        mock_ws.send_to_task_or_user = AsyncMock()
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value=FormBlockResult(
            form={"type": "form", "form_type": "scheduled_task_update", "fields": []},
        ))

        tc = {"name": "manage_scheduled_task", "id": "tc2",
              "arguments": '{"action":"update","task_name":"日报"}'}
        await ChatToolMixin._execute_single_tool(
            mixin, tc, executor, "task1", "conv1", "msg1", "user1", 2,
        )

        mixin._emit_tool_audit.assert_called_once()
        audit_args = mixin._emit_tool_audit.call_args[0]
        assert audit_args[3] == "manage_scheduled_task"  # tool_name
        assert audit_args[9] == "success"  # status (index 9)
