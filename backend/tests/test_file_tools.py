"""
文件工具定义测试

验证 file_tools.py 的工具 Schema、工具集合、路由提示词。
file_list/search/info 已移除（被 code_execute 内 os.listdir/walk/stat 替代）。
"""

import pytest

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
        expected = {"file_read", "file_write", "file_edit"}
        assert FILE_INFO_TOOLS == expected

    def test_file_list_removed(self):
        assert "file_list" not in FILE_INFO_TOOLS
        assert "file_search" not in FILE_INFO_TOOLS


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

    def test_file_edit_requires_path_and_strings(self):
        schema = FILE_TOOL_SCHEMAS["file_edit"]
        assert "path" in schema["required"]
        assert "old_string" in schema["required"]
        assert "new_string" in schema["required"]

    def test_file_list_schema_removed(self):
        assert "file_list" not in FILE_TOOL_SCHEMAS

    def test_file_search_schema_removed(self):
        assert "file_search" not in FILE_TOOL_SCHEMAS


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

    def test_file_write_has_mode_enum(self):
        tools = build_file_tools()
        write_tool = next(
            t for t in tools if t["function"]["name"] == "file_write"
        )
        mode_prop = write_tool["function"]["parameters"]["properties"]["mode"]
        assert "enum" in mode_prop
        assert set(mode_prop["enum"]) == {"overwrite", "append", "create_only"}


class TestFileReadPagesParam:

    def test_pages_in_schema(self):
        schema = FILE_TOOL_SCHEMAS["file_read"]
        assert "pages" in schema["properties"]

    def test_pages_in_build_file_tools(self):
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        params = read_tool["function"]["parameters"]["properties"]
        assert "pages" in params

    def test_file_read_description_mentions_pdf(self):
        tools = build_file_tools()
        read_tool = next(t for t in tools if t["function"]["name"] == "file_read")
        desc = read_tool["function"]["description"]
        assert "PDF" in desc


class TestFileRoutingPrompt:

    def test_mentions_code_execute(self):
        assert "code_execute" in FILE_ROUTING_PROMPT

    def test_mentions_file_read(self):
        assert "file_read" in FILE_ROUTING_PROMPT

    def test_mentions_data_query(self):
        assert "data_query" in FILE_ROUTING_PROMPT

    def test_no_file_list_reference(self):
        assert "file_list" not in FILE_ROUTING_PROMPT

    def test_no_file_search_reference(self):
        assert "file_search" not in FILE_ROUTING_PROMPT
