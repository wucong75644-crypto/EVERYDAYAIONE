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
        expected = {"file_read", "file_write", "file_list", "file_search", "file_info"}
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

    def test_file_info_requires_path(self):
        schema = FILE_TOOL_SCHEMAS["file_info"]
        assert "path" in schema["required"]


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


class TestFileRoutingPrompt:

    def test_mentions_all_tools(self):
        for tool in ["file_read", "file_write", "file_list", "file_search", "file_info"]:
            assert tool in FILE_ROUTING_PROMPT

    def test_mentions_code_execute_for_binary(self):
        assert "code_execute" in FILE_ROUTING_PROMPT
