"""代码执行工具集成测试

验证 code_execute 工具从定义到注册的完整链路。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.code_tools import (
    CODE_INFO_TOOLS,
    CODE_ROUTING_PROMPT,
    CODE_TOOL_SCHEMAS,
    build_code_tools,
)


class TestCodeToolsDefinition:
    """工具定义测试"""

    def test_info_tools_set(self):
        assert "code_execute" in CODE_INFO_TOOLS

    def test_schema_has_required_fields(self):
        schema = CODE_TOOL_SCHEMAS["code_execute"]
        assert "code" in schema["required"]
        assert "description" in schema["required"]

    def test_build_returns_one_tool(self):
        tools = build_code_tools()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "code_execute"

    def test_tool_has_parameters(self):
        tool = build_code_tools()[0]
        params = tool["function"]["parameters"]
        assert "code" in params["properties"]
        assert "description" in params["properties"]

    def test_sandbox_is_pure_computation(self):
        """code_execute 描述不包含数据获取函数"""
        tool = build_code_tools()[0]
        desc = tool["function"]["description"]
        assert "erp_query" not in desc
        assert "web_search" not in desc
        assert "纯计算" in desc or "staging" in desc

    def test_read_file_staging_documented(self):
        """code_execute 描述中说明 read_file 限 staging"""
        tool = build_code_tools()[0]
        desc = tool["function"]["description"]
        assert "read_file" in desc
        assert "staging" in desc

    def test_routing_prompt_not_empty(self):
        assert "code_execute" in CODE_ROUTING_PROMPT
        assert "fetch_all_pages" in CODE_ROUTING_PROMPT

    def test_routing_prompt_mentions_fetch_all_pages(self):
        """CODE_ROUTING_PROMPT 包含 fetch_all_pages 协议"""
        assert "fetch_all_pages" in CODE_ROUTING_PROMPT
        assert "staging" in CODE_ROUTING_PROMPT

    def test_routing_prompt_no_erp_query(self):
        """CODE_ROUTING_PROMPT 不再提及 erp_query"""
        assert "erp_query（" not in CODE_ROUTING_PROMPT
        assert "erp_query_all" not in CODE_ROUTING_PROMPT

    def test_routing_prompt_jsonl_format(self):
        """CODE_ROUTING_PROMPT 说明 JSONL 格式和 io.StringIO 用法"""
        assert "JSONL" in CODE_ROUTING_PROMPT
        assert "io.StringIO" in CODE_ROUTING_PROMPT

    def test_code_execute_desc_jsonl(self):
        """code_execute 描述中包含 JSONL 和 io.StringIO"""
        tool = build_code_tools()[0]
        desc = tool["function"]["description"]
        assert "jsonl" in desc or "JSONL" in desc
        assert "io.StringIO" in desc

    def test_routing_prompt_mentions_local_db_export(self):
        """CODE_ROUTING_PROMPT 提及 local_db_export"""
        assert "local_db_export" in CODE_ROUTING_PROMPT


class TestAgentToolsIntegration:
    """agent_tools.py 集成测试"""

    def test_code_execute_in_info_tools(self):
        from config.agent_tools import INFO_TOOLS
        assert "code_execute" in INFO_TOOLS

    def test_code_execute_in_all_tools(self):
        from config.agent_tools import ALL_TOOLS
        assert "code_execute" in ALL_TOOLS

    def test_code_execute_in_schemas(self):
        from config.agent_tools import TOOL_SCHEMAS
        assert "code_execute" in TOOL_SCHEMAS

    def test_validate_tool_call_accepts(self):
        from config.agent_tools import validate_tool_call
        assert validate_tool_call(
            "code_execute",
            {"code": "1+1", "description": "test"},
        )

    def test_validate_tool_call_rejects_missing_required(self):
        from config.agent_tools import validate_tool_call
        assert not validate_tool_call(
            "code_execute",
            {"code": "1+1"},  # missing description
        )

    def test_code_execute_in_tool_schemas(self):
        """code_execute schema registered with required fields"""
        from config.agent_tools import TOOL_SCHEMAS
        schema = TOOL_SCHEMAS["code_execute"]
        assert "code" in schema["required"]
        assert "description" in schema["required"]


class TestConfigSettings:
    """core/config.py 沙盒配置测试"""

    def test_sandbox_defaults(self):
        from core.config import Settings
        # 验证默认值（不加载 .env）
        fields = Settings.model_fields
        assert "sandbox_enabled" in fields
        assert "sandbox_timeout" in fields
        assert "sandbox_max_result_chars" in fields
        assert "sandbox_api_concurrency" in fields
        assert "sandbox_max_pages" in fields

    def test_sandbox_default_values(self):
        from core.config import Settings
        defaults = {
            name: field.default
            for name, field in Settings.model_fields.items()
            if name.startswith("sandbox_")
        }
        assert defaults["sandbox_enabled"] is True
        assert defaults["sandbox_timeout"] == 120.0
        assert defaults["sandbox_max_result_chars"] == 8000
        assert defaults["sandbox_api_concurrency"] == 10
        assert defaults["sandbox_max_pages"] == 200


class TestToolExecutorRegistration:
    """tool_executor.py handler 注册测试"""

    def test_code_execute_handler_registered(self):
        from services.tool_executor import ToolExecutor
        mock_db = MagicMock()
        executor = ToolExecutor(mock_db, "user1", "conv1")
        assert "code_execute" in executor._handlers

    def test_fetch_all_pages_registered_for_org(self):
        """企业用户注册 fetch_all_pages"""
        from services.tool_executor import ToolExecutor
        mock_db = MagicMock()
        executor = ToolExecutor(mock_db, "user1", "conv1", org_id="org1")
        assert "fetch_all_pages" in executor._handlers

    def test_fetch_all_pages_not_for_guest(self):
        """散客不注册 fetch_all_pages"""
        from services.tool_executor import ToolExecutor
        mock_db = MagicMock()
        executor = ToolExecutor(mock_db, "user1", "conv1", org_id=None)
        assert "fetch_all_pages" not in executor._handlers


class TestFetchAllPagesExecution:
    """_fetch_all_pages 执行逻辑测试"""

    def _make_executor(self):
        from services.tool_executor import ToolExecutor
        return ToolExecutor(MagicMock(), "user1", "conv1", org_id="org1")

    @pytest.mark.asyncio
    async def test_missing_tool_param(self):
        """缺少 tool 参数返回错误"""
        executor = self._make_executor()
        result = await executor._fetch_all_pages({"action": "order_list"})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_missing_action_param(self):
        """缺少 action 参数返回错误"""
        executor = self._make_executor()
        result = await executor._fetch_all_pages({"tool": "erp_trade_query"})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_page_size_min_20(self):
        """page_size 最小值为 20"""
        executor = self._make_executor()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {"list": [], "total": 0}

        with patch.object(executor, "_get_erp_dispatcher", return_value=mock_dispatcher), \
             patch("core.config.get_settings") as mock_s:
            mock_s.return_value.sandbox_api_concurrency = 10
            mock_s.return_value.file_workspace_root = "/tmp/test_workspace"
            result = await executor._fetch_all_pages({
                "tool": "erp_trade_query", "action": "order_list",
                "page_size": 5,  # 小于20
            })
        # page_size 被强制为 20
        call_params = mock_dispatcher.execute_raw.call_args[0][2]
        assert call_params["page_size"] >= 20

    @pytest.mark.asyncio
    async def test_path_traversal_sanitized(self):
        """tool_name 包含 ../ 时路径安全"""
        executor = self._make_executor()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": 1}], "total": 1,
        }

        with patch.object(executor, "_get_erp_dispatcher", return_value=mock_dispatcher), \
             patch("core.config.get_settings") as mock_s:
            mock_s.return_value.sandbox_api_concurrency = 10
            mock_s.return_value.file_workspace_root = "/tmp/test_workspace"
            result = await executor._fetch_all_pages({
                "tool": "../../../etc", "action": "passwd",
            })
        # 文件名中不包含 ../
        assert "../" not in result

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """查询结果为空"""
        executor = self._make_executor()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {"list": [], "total": 0}

        with patch.object(executor, "_get_erp_dispatcher", return_value=mock_dispatcher), \
             patch("core.config.get_settings") as mock_s:
            mock_s.return_value.sandbox_api_concurrency = 10
            mock_s.return_value.file_workspace_root = "/tmp/test_workspace"
            result = await executor._fetch_all_pages({
                "tool": "erp_trade_query", "action": "order_list",
            })
        assert "为空" in result
