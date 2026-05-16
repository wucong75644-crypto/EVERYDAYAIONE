"""
文件工具定义单元测试
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
        expected = {"file_read", "file_delete", "file_search", "restore_file"}
        assert FILE_INFO_TOOLS == expected


class TestFileToolSchemas:

    def test_all_tools_have_schema(self):
        for tool_name in FILE_INFO_TOOLS:
            assert tool_name in FILE_TOOL_SCHEMAS

    def test_file_read_requires_path(self):
        schema = FILE_TOOL_SCHEMAS["file_read"]
        assert "path" in schema["required"]

    def test_file_delete_requires_files(self):
        schema = FILE_TOOL_SCHEMAS["file_delete"]
        assert "files" in schema["required"]

    def test_file_search_no_required(self):
        schema = FILE_TOOL_SCHEMAS["file_search"]
        assert schema["required"] == []


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

    def test_no_file_write_or_edit(self):
        """file_write 和 file_edit 已移除"""
        tools = build_file_tools()
        names = {t["function"]["name"] for t in tools}
        assert "file_write" not in names
        assert "file_edit" not in names


class TestFileReadImageOnly:
    """file_read 现在仅支持图片，不含 pages 参数"""

    def test_no_pages_in_schema(self):
        schema = FILE_TOOL_SCHEMAS["file_read"]
        assert "pages" not in schema["properties"]

    def test_no_pages_in_build_file_tools(self):
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        params = read_tool["function"]["parameters"]["properties"]
        assert "pages" not in params

    def test_only_path_required(self):
        """只有 path 是必传参数"""
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        required = read_tool["function"]["parameters"]["required"]
        assert required == ["path"]

    def test_file_read_description_mentions_image(self):
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        desc = read_tool["function"]["description"]
        assert "图片" in desc or "png" in desc

    def test_file_read_description_no_pdf_pages(self):
        """file_read 不再处理 PDF"""
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        desc = read_tool["function"]["description"]
        # PDF 在 code_execute 中读取，不在 file_read 中
        assert "pages" not in desc


class TestFileRoutingPrompt:

    def test_mentions_core_tools(self):
        for tool in ["file_read", "file_search", "restore_file"]:
            assert tool in FILE_ROUTING_PROMPT

    def test_no_file_write_or_edit_in_routing(self):
        """路由提示词不再提及已移除的工具"""
        assert "file_write" not in FILE_ROUTING_PROMPT
        assert "file_edit" not in FILE_ROUTING_PROMPT

    def test_mentions_code_execute(self):
        assert "code_execute" in FILE_ROUTING_PROMPT

    def test_mentions_file_read_for_data(self):
        """数据查询通过 file_read + code_execute + duckdb"""
        assert "file_read" in FILE_ROUTING_PROMPT
        assert "duckdb" in FILE_ROUTING_PROMPT
