"""
ToolLoopContext 单元测试

覆盖：update_from_result, build_context_prompt, _extract_identified_codes, discovered_tools
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest


class TestToolLoopContextInit:
    """初始状态测试"""

    def test_empty_on_create(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        assert ctx.identified_codes == {}
        assert ctx.sync_warnings == []
        assert ctx.used_tools == []
        assert ctx.failed_tools == []
        assert ctx.discovered_tools == set()

    def test_no_context_prompt_when_empty(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        assert ctx.build_context_prompt() is None


class TestUpdateFromResult:
    """update_from_result 测试"""

    def test_tracks_used_tools(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.update_from_result("local_stock_query", "库存100件", False)
        assert "local_stock_query" in ctx.used_tools

    def test_tracks_failed_tools(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.update_from_result("erp_trade_query", "工具执行失败", True)
        assert "erp_trade_query" in ctx.failed_tools

    def test_extracts_sync_warning(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.update_from_result(
            "local_stock_query",
            "库存100件\n⚠ 数据同步延迟3分钟",
            False,
        )
        assert len(ctx.sync_warnings) == 1
        assert "同步" in ctx.sync_warnings[0]

    def test_no_duplicate_sync_warnings(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.update_from_result("t1", "⚠ 数据同步延迟", False)
        ctx.update_from_result("t2", "⚠ 数据同步延迟", False)
        assert len(ctx.sync_warnings) == 1

    def test_discovers_tools_from_erp_api_search(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext(org_id="test_org")
        result = (
            "找到 2 个匹配:\n"
            "- erp_trade_query:order_list — 订单查询\n"
            "- erp_purchase_query:purchase_order_list — 采购查询\n"
            "💡 推荐 erp_trade_query:order_list"
        )
        ctx.update_from_result("erp_api_search", result, False)
        assert "erp_trade_query" in ctx.discovered_tools
        assert "erp_purchase_query" in ctx.discovered_tools

    def test_no_discovery_on_error(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.update_from_result("erp_api_search", "搜索失败", True)
        assert len(ctx.discovered_tools) == 0

    def test_no_discovery_from_non_search_tool(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.update_from_result("local_stock_query", "erp_trade_query 相关", False)
        assert len(ctx.discovered_tools) == 0


class TestBuildContextPrompt:
    """build_context_prompt 测试"""

    def test_shows_identified_codes(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.identified_codes = {"YSL01": "YSL01-RED-M"}
        prompt = ctx.build_context_prompt()
        assert "YSL01" in prompt
        assert "YSL01-RED-M" in prompt
        assert "无需再次识别" in prompt

    def test_shows_sync_warnings(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.sync_warnings = ["延迟3分钟"]
        prompt = ctx.build_context_prompt()
        assert "同步延迟" in prompt

    def test_shows_failed_tools(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.failed_tools = ["erp_trade_query"]
        prompt = ctx.build_context_prompt()
        assert "erp_trade_query" in prompt
        assert "换其他工具" in prompt

    def test_combines_multiple_contexts(self):
        from services.handlers.tool_loop_context import ToolLoopContext
        ctx = ToolLoopContext()
        ctx.identified_codes = {"A": "B"}
        ctx.sync_warnings = ["延迟"]
        ctx.failed_tools = ["tool_x"]
        prompt = ctx.build_context_prompt()
        assert "已识别编码" in prompt
        assert "同步延迟" in prompt
        assert "tool_x" in prompt


class TestExtractToolNamesAndCoreTools:
    """chat_tools.py 新增函数测试"""

    def test_get_core_tools_returns_subset(self):
        from config.chat_tools import get_core_tools, get_chat_tools
        core = get_core_tools(org_id="test")
        full = get_chat_tools(org_id="test")
        assert len(core) < len(full)
        assert len(core) >= 6  # ERP Agent 模式：7 个核心工具

    def test_get_core_tools_contains_essentials(self):
        from config.chat_tools import get_core_tools
        core = get_core_tools(org_id="test")
        names = {t["function"]["name"] for t in core}
        assert "erp_agent" in names
        assert "erp_api_search" in names
        assert "web_search" in names

    def test_get_tools_by_names(self):
        from config.chat_tools import get_tools_by_names
        result = get_tools_by_names({"erp_trade_query"}, org_id="test")
        assert len(result) == 1
        assert result[0]["function"]["name"] == "erp_trade_query"

    def test_get_tools_by_names_empty(self):
        from config.chat_tools import get_tools_by_names
        result = get_tools_by_names({"nonexistent"}, org_id="test")
        assert len(result) == 0

    def test_extract_tool_names_from_search_result(self):
        from config.chat_tools import extract_tool_names_from_result
        text = "推荐 erp_trade_query:order_list\n- erp_purchase_query:list"
        names = extract_tool_names_from_result(text, org_id="test_org")
        assert "erp_trade_query" in names
        assert "erp_purchase_query" in names

    def test_extract_excludes_core_tools(self):
        from config.chat_tools import extract_tool_names_from_result
        # erp_api_search 是核心工具，不应出现在 discovered 里
        text = "erp_api_search 和 erp_trade_query"
        names = extract_tool_names_from_result(text, org_id="test_org")
        assert "erp_api_search" not in names
        assert "erp_trade_query" in names

    def test_get_tool_system_prompt_not_empty(self):
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert len(prompt) > 100
        assert "工具使用规则" in prompt

    def test_tool_group_enum(self):
        from config.chat_tools import ToolGroup
        assert ToolGroup.ERP_LOCAL.value == "erp_local"
        assert ToolGroup.MEDIA.value == "media"


class TestToolResultEnvelope:
    """tool_result_envelope.wrap 测试（替代已删除的 _summarize_if_needed）"""

    def test_short_result_unchanged(self):
        from services.agent.tool_result_envelope import wrap
        result = "库存100件"
        assert wrap("local_stock_query", result) == result

    def test_long_result_truncated_with_signal(self):
        from services.agent.tool_result_envelope import wrap, MAIN_AGENT_BUDGET
        result = "x" * 5000
        wrapped = wrap("some_tool", result)
        assert len(wrapped) < len(result)
        assert "截断" in wrapped
        assert "5000" in wrapped

    def test_empty_result_unchanged(self):
        from services.agent.tool_result_envelope import wrap
        assert wrap("tool", "") == ""
        assert wrap("tool", None) is None

    def test_erp_agent_budget(self):
        from services.agent.tool_result_envelope import wrap, ERP_AGENT_RESULT_BUDGET
        result = "x" * (ERP_AGENT_RESULT_BUDGET + 500)
        wrapped = wrap("erp_agent", result)
        assert "截断" in wrapped

    def test_no_truncate_tools(self):
        from services.agent.tool_result_envelope import wrap
        result = "x" * 10000
        assert wrap("generate_image", result) == result

    def test_erp_result_preserves_summary_lines(self):
        from services.agent.tool_result_envelope import wrap_for_erp_agent
        lines = ["订单查询结果"] + [f"订单{i}" for i in range(100)] + ["合计：100单"]
        result = "\n".join(lines)
        wrapped = wrap_for_erp_agent("local_order_query", result)
        assert "订单查询结果" in wrapped
        assert "合计：100单" in wrapped


class TestToolExecutorNewHandlers:
    """ToolExecutor 新增 handler 测试"""

    @pytest.mark.asyncio
    async def test_web_search_registered(self):
        from unittest.mock import MagicMock
        from services.tool_executor import ToolExecutor
        exe = ToolExecutor(db=MagicMock(), user_id="t", conversation_id="t", org_id=None)
        assert "web_search" in exe._handlers

    @pytest.mark.asyncio
    async def test_generate_image_registered(self):
        from unittest.mock import MagicMock
        from services.tool_executor import ToolExecutor
        exe = ToolExecutor(db=MagicMock(), user_id="t", conversation_id="t", org_id=None)
        assert "generate_image" in exe._handlers

    @pytest.mark.asyncio
    async def test_generate_video_registered(self):
        from unittest.mock import MagicMock
        from services.tool_executor import ToolExecutor
        exe = ToolExecutor(db=MagicMock(), user_id="t", conversation_id="t", org_id=None)
        assert "generate_video" in exe._handlers

    @pytest.mark.asyncio
    async def test_generate_image_handler_registered(self):
        """generate_image 已从桩升级为真实工具"""
        from unittest.mock import MagicMock
        from services.tool_executor import ToolExecutor
        exe = ToolExecutor(db=MagicMock(), user_id="t", conversation_id="t", org_id=None)
        assert exe._handlers.get("generate_image") == exe._generate_image
        assert exe._handlers.get("generate_video") == exe._generate_video
