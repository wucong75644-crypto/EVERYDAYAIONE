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

    def test_erp_query_all_return_format_documented(self):
        """code_execute 描述中包含 erp_query_all 说明"""
        tool = build_code_tools()[0]
        desc = tool["function"]["description"]
        assert "erp_query_all" in desc
        assert "total" in desc

    def test_erp_query_return_documented(self):
        """code_execute 描述中包含 erp_query 说明"""
        tool = build_code_tools()[0]
        desc = tool["function"]["description"]
        assert "erp_query(" in desc
        assert "erp_query_all(" in desc

    def test_routing_prompt_not_empty(self):
        assert "code_execute" in CODE_ROUTING_PROMPT
        assert "route_to_chat" in CODE_ROUTING_PROMPT

    def test_routing_prompt_no_routing_directives(self):
        """CODE_ROUTING_PROMPT 不包含链路指令（能力驱动架构）"""
        assert "数据聚合" not in CODE_ROUTING_PROMPT
        assert "典型场景" not in CODE_ROUTING_PROMPT
        assert "→ code_execute" not in CODE_ROUTING_PROMPT
        assert "→ 仍用" not in CODE_ROUTING_PROMPT

    def test_code_execute_desc_no_scenario_guidance(self):
        """code_execute 描述不包含场景引导"""
        tool = build_code_tools()[0]
        desc = tool["function"]["description"]
        assert "使用场景" not in desc
        assert "适用于需要" not in desc

    def test_code_execute_desc_mentions_cost(self):
        """code_execute 描述说明了 erp_query_all 的耗时代价"""
        tool = build_code_tools()[0]
        desc = tool["function"]["description"]
        assert "耗时较长" in desc
        assert "60秒" in desc


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
