"""
文件工具定义测试

验证 file_tools.py 的工具 Schema、工具集合、路由提示词。
"""

import pytest

from config.file_tools import (
    FILE_INFO_TOOLS,
    FILE_TOOL_SCHEMAS,
    FILE_ROUTING_PROMPT,
    build_file_tools,
)


class TestFileInfoTools:

    def test_has_five_tools(self):
        assert len(FILE_INFO_TOOLS) == 5

    def test_tool_names(self):
        expected = {"file_read", "file_write", "file_edit", "file_list", "file_search"}
        assert FILE_INFO_TOOLS == expected


class TestFileToolSchemas:

    def test_all_tools_have_schema(self):
        for tool_name in FILE_INFO_TOOLS:
            assert tool_name in FILE_TOOL_SCHEMAS

    def test_file_read_requires_path(self):
        schema = FILE_TOOL_SCHEMAS["file_read"]
        assert "path" in schema["required"]

    def test_file_write_requires_path_and_content(self):
        schema = FILE_TOOL_SCHEMAS["file_write"]
        assert "path" in schema["required"]
        assert "content" in schema["required"]

    def test_file_list_no_required(self):
        schema = FILE_TOOL_SCHEMAS["file_list"]
        assert schema["required"] == []

    def test_file_search_requires_keyword(self):
        schema = FILE_TOOL_SCHEMAS["file_search"]
        assert "keyword" in schema["required"]

    def test_file_edit_requires_path_and_strings(self):
        schema = FILE_TOOL_SCHEMAS["file_edit"]
        assert "path" in schema["required"]
        assert "old_string" in schema["required"]
        assert "new_string" in schema["required"]


class TestBuildFileTools:

    def test_returns_five_tools(self):
        tools = build_file_tools()
        assert len(tools) == 5

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

    def test_file_write_has_mode_enum(self):
        tools = build_file_tools()
        write_tool = next(
            t for t in tools if t["function"]["name"] == "file_write"
        )
        mode_prop = write_tool["function"]["parameters"]["properties"]["mode"]
        assert "enum" in mode_prop
        assert set(mode_prop["enum"]) == {"overwrite", "append", "create_only"}


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

    def test_mentions_all_tools(self):
        for tool in ["file_read", "file_write", "file_list", "file_search"]:
            assert tool in FILE_ROUTING_PROMPT

    def test_mentions_code_execute_for_binary(self):
        assert "code_execute" in FILE_ROUTING_PROMPT

    def test_mentions_pdf_in_routing(self):
        assert "PDF" in FILE_ROUTING_PROMPT or "pdf" in FILE_ROUTING_PROMPT

    def test_mentions_image_in_routing(self):
        assert "图片" in FILE_ROUTING_PROMPT or "png" in FILE_ROUTING_PROMPT.lower()
