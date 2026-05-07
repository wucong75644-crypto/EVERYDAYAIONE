"""
config/common_tools.py 单元测试

覆盖：build_common_tools() 返回的工具列表完整性和 schema 格式
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config.common_tools import build_common_tools


class TestBuildCommonTools:
    """build_common_tools() 测试"""

    def test_returns_list(self):
        tools = build_common_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_all_function_type(self):
        tools = build_common_tools()
        for tool in tools:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]

    def test_contains_expected_tools(self):
        """包含所有预期的通用工具"""
        tools = build_common_tools()
        names = {t["function"]["name"] for t in tools}
        expected = {
            "erp_agent", "erp_analyze", "erp_api_search",
            "search_knowledge", "web_search",
            "generate_image", "generate_video", "image_agent",
            "data_query", "manage_scheduled_task",
        }
        for name in expected:
            assert name in names, f"Missing tool: {name}"

    def test_no_duplicates(self):
        tools = build_common_tools()
        names = [t["function"]["name"] for t in tools]
        assert len(names) == len(set(names))

    def test_erp_agent_has_task_param(self):
        tools = build_common_tools()
        erp_agent = next(t for t in tools if t["function"]["name"] == "erp_agent")
        assert "task" in erp_agent["function"]["parameters"]["required"]

    def test_data_query_has_file_param(self):
        tools = build_common_tools()
        dq = next(t for t in tools if t["function"]["name"] == "data_query")
        assert "file" in dq["function"]["parameters"]["required"]

    def test_tool_count(self):
        """工具数量在预期范围"""
        tools = build_common_tools()
        assert 8 <= len(tools) <= 15


class TestCommonToolsIntegration:
    """拆分后与 chat_tools 集成验证"""

    def test_chat_tools_imports_common(self):
        """chat_tools 能正确导入 build_common_tools"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id="test_org")
        names = {t["function"]["name"] for t in tools}
        # common_tools 里的工具应出现在 chat_tools 返回中
        assert "erp_agent" in names
        assert "web_search" in names
        assert "data_query" in names

    def test_guest_still_has_common_tools(self):
        """散客仍能获取通用工具"""
        from config.chat_tools import get_chat_tools
        tools = get_chat_tools(org_id=None)
        names = {t["function"]["name"] for t in tools}
        assert "web_search" in names
        assert "search_knowledge" in names
