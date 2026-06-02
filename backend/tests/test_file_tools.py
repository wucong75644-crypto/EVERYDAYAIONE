"""
文件工具定义单元测试

P2 后：file_read 工具已删除，FILE_INFO_TOOLS 含 4 个工具。
图片读取走 file_search（命中单张图自动返回多模态）；
PDF/Word/PPT 走 code_execute + 对应库。
"""

from config.file_tools import (
    FILE_INFO_TOOLS,
    FILE_TOOL_SCHEMAS,
    FILE_ROUTING_PROMPT,
    build_file_tools,
)


class TestFileInfoTools:

    def test_has_four_tools(self):
        assert len(FILE_INFO_TOOLS) == 4

    def test_tool_names(self):
        expected = {"file_analyze", "file_delete", "file_search", "restore_file"}
        assert FILE_INFO_TOOLS == expected

    def test_file_read_removed(self):
        """file_read 工具已删除（多模态模型下冗余）"""
        assert "file_read" not in FILE_INFO_TOOLS


class TestFileToolSchemas:

    def test_all_tools_have_schema(self):
        for tool_name in FILE_INFO_TOOLS:
            assert tool_name in FILE_TOOL_SCHEMAS

    def test_file_delete_requires_files(self):
        schema = FILE_TOOL_SCHEMAS["file_delete"]
        assert "files" in schema["required"]

    def test_file_search_no_required(self):
        schema = FILE_TOOL_SCHEMAS["file_search"]
        assert schema["required"] == []

    def test_no_file_read_schema(self):
        assert "file_read" not in FILE_TOOL_SCHEMAS


class TestBuildFileTools:

    def test_returns_four_tools(self):
        tools = build_file_tools()
        assert len(tools) == 4

    def test_all_function_type(self):
        tools = build_file_tools()
        for tool in tools:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]

    def test_tool_names_match(self):
        tools = build_file_tools()
        names = {t["function"]["name"] for t in tools}
        assert names == FILE_INFO_TOOLS

    def test_no_file_read_or_write_or_edit(self):
        """file_read（多模态模型下冗余）+ file_write/edit 已移除"""
        tools = build_file_tools()
        names = {t["function"]["name"] for t in tools}
        assert "file_read" not in names
        assert "file_write" not in names
        assert "file_edit" not in names


class TestFileRoutingPrompt:

    def test_mentions_core_tools(self):
        for tool in ["file_search", "restore_file"]:
            assert tool in FILE_ROUTING_PROMPT

    def test_no_file_read_in_routing(self):
        """路由提示词不再引导 LLM 用已删除的 file_read"""
        assert "file_read" not in FILE_ROUTING_PROMPT

    def test_no_file_write_or_edit_in_routing(self):
        assert "file_write" not in FILE_ROUTING_PROMPT
        assert "file_edit" not in FILE_ROUTING_PROMPT

    def test_mentions_code_execute(self):
        assert "code_execute" in FILE_ROUTING_PROMPT

    def test_mentions_duckdb_for_data(self):
        """数据查询通过 file_analyze → code_execute + duckdb"""
        assert "duckdb" in FILE_ROUTING_PROMPT

    def test_mentions_multimodal_for_image(self):
        """图片走 file_search 自动多模态注入"""
        assert "多模态" in FILE_ROUTING_PROMPT or "视觉" in FILE_ROUTING_PROMPT
