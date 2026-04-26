"""
config/chat_tools.py 单元测试

覆盖：SafetyLevel 枚举、get_safety_level()、is_concurrency_safe()、get_chat_tools()
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest


class TestSafetyLevel:
    """SafetyLevel 枚举测试"""

    def test_enum_values(self):
        from config.chat_tools import SafetyLevel
        assert SafetyLevel.SAFE.value == "safe"
        assert SafetyLevel.CONFIRM.value == "confirm"
        assert SafetyLevel.DANGEROUS.value == "dangerous"

    def test_enum_is_str(self):
        from config.chat_tools import SafetyLevel
        assert isinstance(SafetyLevel.SAFE, str)
        assert SafetyLevel.SAFE == "safe"


class TestGetSafetyLevel:
    """get_safety_level() 测试"""

    def test_erp_query_is_safe(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("erp_product_query") == SafetyLevel.SAFE

    def test_local_query_is_safe(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("local_stock_query") == SafetyLevel.SAFE

    def test_search_is_safe(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("erp_api_search") == SafetyLevel.SAFE
        assert get_safety_level("web_search") == SafetyLevel.SAFE

    def test_generate_image_is_confirm(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("generate_image") == SafetyLevel.CONFIRM

    def test_generate_video_is_confirm(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("generate_video") == SafetyLevel.CONFIRM

    def test_erp_execute_is_dangerous(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("erp_execute") == SafetyLevel.DANGEROUS

    def test_trigger_sync_is_dangerous(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("trigger_erp_sync") == SafetyLevel.DANGEROUS

    def test_unknown_tool_defaults_safe(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("nonexistent_tool") == SafetyLevel.SAFE


class TestIsConcurrencySafe:
    """is_concurrency_safe() 测试"""

    def test_query_tools_are_safe(self):
        from config.chat_tools import is_concurrency_safe
        safe_tools = [
            "erp_product_query", "erp_trade_query",
            "local_stock_query", "local_data",
            "erp_api_search", "search_knowledge", "web_search",
            "social_crawler", "code_execute",
        ]
        for tool in safe_tools:
            assert is_concurrency_safe(tool), f"{tool} should be concurrent safe"

    def test_write_tools_are_not_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert not is_concurrency_safe("erp_execute")
        assert not is_concurrency_safe("trigger_erp_sync")

    def test_generate_tools_are_not_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert not is_concurrency_safe("generate_image")
        assert not is_concurrency_safe("generate_video")

    def test_unknown_tool_not_safe(self):
        """未注册工具默认不安全（保守策略）"""
        from config.chat_tools import is_concurrency_safe
        assert not is_concurrency_safe("nonexistent_tool")


class TestGetChatTools:
    """get_chat_tools() 测试"""

    def test_returns_list(self):
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_tool_format(self):
        """每个工具符合 OpenAI function calling 格式"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools()
        for tool in tools:
            assert tool.get("type") == "function"
            func = tool.get("function")
            assert func is not None
            assert "name" in func
            assert "description" in func or "parameters" in func

    def test_no_duplicates(self):
        """工具名不重复（企业用户）"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id="test_org")
        names = [t["function"]["name"] for t in tools]
        assert len(names) == len(set(names)), f"Duplicates: {[n for n in names if names.count(n) > 1]}"

    def test_contains_key_tools(self):
        """企业用户包含核心ERP工具"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id="test_org")
        names = {t["function"]["name"] for t in tools}
        expected = {
            "erp_api_search", "search_knowledge", "web_search",
            "generate_image", "generate_video",
            "erp_product_query", "local_stock_query",
        }
        for name in expected:
            assert name in names, f"Missing tool: {name}"

    def test_total_count(self):
        """企业用户工具总数在合理范围"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id="test_org")
        assert 20 <= len(tools) <= 40, f"Got {len(tools)} tools"

    def test_guest_no_erp_tools(self):
        """散客不加载ERP工具"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id=None)
        names = {t["function"]["name"] for t in tools}
        assert "local_stock_query" not in names
        assert "erp_product_query" not in names
        # 通用工具仍在
        assert "web_search" in names
        assert "generate_image" in names

    def test_guest_tool_count(self):
        """散客工具数量少于企业"""
        from config.chat_tools import get_chat_tools
        guest = get_chat_tools(org_id=None)
        enterprise = get_chat_tools(org_id="test_org")
        assert len(guest) < len(enterprise)


class TestAskUserTool:
    """ask_user 工具验证"""

    def test_ask_user_in_chat_tools(self):
        """get_chat_tools 返回列表中包含 ask_user"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id="test_org")
        names = {t["function"]["name"] for t in tools}
        assert "ask_user" in names

    def test_ask_user_in_guest_tools(self):
        """散客也有 ask_user"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id=None)
        names = {t["function"]["name"] for t in tools}
        assert "ask_user" in names

    def test_ask_user_in_core_tools(self):
        """ask_user 在 _CORE_TOOLS 集合中"""
        from config.chat_tools import _CORE_TOOLS
        assert "ask_user" in _CORE_TOOLS

    def test_ask_user_schema(self):
        """ask_user 工具 schema 包含 message 参数"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id="test_org")
        ask_user = next(t for t in tools if t["function"]["name"] == "ask_user")
        params = ask_user["function"]["parameters"]
        assert "message" in params["properties"]


class TestCoreToolsExpanded:
    """扩展后的 _CORE_TOOLS 验证"""

    def test_core_tools_count(self):
        from config.chat_tools import get_core_tools
        tools = get_core_tools(org_id="test")
        # 14 个核心工具（erp_analyze 加入 GENERAL 域后通过 domain filter）
        assert len(tools) == 14

    def test_core_tools_include_file_tools(self):
        from config.chat_tools import get_core_tools
        names = {t["function"]["name"] for t in get_core_tools(org_id="test")}
        for ft in ("file_read", "file_write", "file_list", "file_search", "file_info"):
            assert ft in names, f"{ft} 应在核心工具中"

    def test_core_tools_include_crawler(self):
        from config.chat_tools import get_core_tools
        names = {t["function"]["name"] for t in get_core_tools(org_id="test")}
        assert "social_crawler" in names

    def test_core_tools_include_media(self):
        from config.chat_tools import get_core_tools
        names = {t["function"]["name"] for t in get_core_tools(org_id="test")}
        assert "generate_image" in names
        assert "generate_video" in names


class TestToolsForMode:
    """get_tools_for_mode 按权限模式过滤"""

    def test_auto_mode_returns_all_core(self):
        from config.chat_tools import get_tools_for_mode, get_core_tools
        auto = get_tools_for_mode("auto", org_id="test")
        core = get_core_tools(org_id="test")
        assert len(auto) == len(core)

    def test_ask_mode_returns_all_core(self):
        from config.chat_tools import get_tools_for_mode, get_core_tools
        ask = get_tools_for_mode("ask", org_id="test")
        core = get_core_tools(org_id="test")
        assert len(ask) == len(core)

    def test_plan_mode_blocks_execution_tools(self):
        from config.chat_tools import get_tools_for_mode, _PLAN_MODE_BLOCKED
        plan = get_tools_for_mode("plan", org_id="test")
        plan_names = {t["function"]["name"] for t in plan}
        for blocked in _PLAN_MODE_BLOCKED:
            assert blocked not in plan_names, f"{blocked} 不应在 plan 模式工具列表中"

    def test_plan_mode_keeps_erp_analyze(self):
        from config.chat_tools import get_tools_for_mode
        plan = get_tools_for_mode("plan", org_id="test")
        plan_names = {t["function"]["name"] for t in plan}
        assert "erp_analyze" in plan_names

    def test_plan_mode_keeps_ask_user(self):
        from config.chat_tools import get_tools_for_mode
        plan = get_tools_for_mode("plan", org_id="test")
        plan_names = {t["function"]["name"] for t in plan}
        assert "ask_user" in plan_names

    def test_plan_mode_keeps_search_tools(self):
        from config.chat_tools import get_tools_for_mode
        plan = get_tools_for_mode("plan", org_id="test")
        plan_names = {t["function"]["name"] for t in plan}
        assert "search_knowledge" in plan_names
        assert "web_search" in plan_names

    def test_plan_mode_tool_count(self):
        from config.chat_tools import get_tools_for_mode, _PLAN_MODE_BLOCKED
        auto = get_tools_for_mode("auto", org_id="test")
        plan = get_tools_for_mode("plan", org_id="test")
        assert len(plan) == len(auto) - len(_PLAN_MODE_BLOCKED)


class TestErpAnalyzeDomain:
    """erp_analyze 域注册验证"""

    def test_erp_analyze_in_general_domain(self):
        from config.tool_domains import TOOL_DOMAINS, ToolDomain
        assert "erp_analyze" in TOOL_DOMAINS
        assert TOOL_DOMAINS["erp_analyze"] == ToolDomain.GENERAL

    def test_erp_analyze_passes_domain_filter(self):
        from config.tool_domains import can_access
        assert can_access("erp_analyze", "general") is True

    def test_erp_analyze_blocked_for_erp_domain(self):
        from config.tool_domains import can_access
        assert can_access("erp_analyze", "erp") is False


class TestFileConcurrencySafe:
    """file 工具并发安全标记"""

    def test_file_read_is_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert is_concurrency_safe("file_read")

    def test_file_list_is_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert is_concurrency_safe("file_list")

    def test_file_search_is_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert is_concurrency_safe("file_search")

    def test_file_info_is_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert is_concurrency_safe("file_info")

    def test_file_write_is_not_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert not is_concurrency_safe("file_write")
