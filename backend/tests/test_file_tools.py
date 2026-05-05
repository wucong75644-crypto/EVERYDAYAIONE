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

    def test_has_three_tools(self):
        assert len(FILE_INFO_TOOLS) == 3

    def test_tool_names(self):
        expected = {"file_read", "file_list", "file_search"}
        assert FILE_INFO_TOOLS == expected


class TestFileToolSchemas:

    def test_all_tools_have_schema(self):
        for tool_name in FILE_INFO_TOOLS:
            assert tool_name in FILE_TOOL_SCHEMAS

    def test_file_read_requires_path(self):
        schema = FILE_TOOL_SCHEMAS["file_read"]
        assert "path" in schema["required"]

    def test_file_list_no_required(self):
        schema = FILE_TOOL_SCHEMAS["file_list"]
        assert schema["required"] == []

    def test_file_search_requires_keyword(self):
        schema = FILE_TOOL_SCHEMAS["file_search"]
        assert "keyword" in schema["required"]


class TestBuildFileTools:

    def test_returns_three_tools(self):
        tools = build_file_tools()
        assert len(tools) == 3

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


class TestFileReadPagesParam:
    """file_read 的 pages 参数在 schema 和工具定义中正确存在"""

    def test_pages_in_schema(self):
        schema = FILE_TOOL_SCHEMAS["file_read"]
        assert "pages" in schema["properties"]
        assert schema["properties"]["pages"]["type"] == "string"

    def test_pages_in_build_file_tools(self):
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        params = read_tool["function"]["parameters"]["properties"]
        assert "pages" in params
        assert params["pages"]["type"] == "string"
        assert "PDF" in params["pages"]["description"]

    def test_pages_not_required(self):
        """pages 是可选参数"""
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        required = read_tool["function"]["parameters"]["required"]
        assert "pages" not in required

    def test_file_read_description_mentions_pdf(self):
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        desc = read_tool["function"]["description"]
        assert "PDF" in desc
        assert "pages" in desc

    def test_file_read_description_mentions_image(self):
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        desc = read_tool["function"]["description"]
        assert "图片" in desc or "png" in desc


class TestFileRoutingPrompt:

    def test_mentions_core_tools(self):
        for tool in ["file_read", "file_list", "file_search"]:
            assert tool in FILE_ROUTING_PROMPT

    def test_no_file_write_or_edit_in_routing(self):
        """路由提示词不再提及已移除的工具"""
        assert "file_write" not in FILE_ROUTING_PROMPT
        assert "file_edit" not in FILE_ROUTING_PROMPT

    def test_mentions_code_execute(self):
        assert "code_execute" in FILE_ROUTING_PROMPT

    def test_mentions_data_query(self):
        assert "data_query" in FILE_ROUTING_PROMPT
