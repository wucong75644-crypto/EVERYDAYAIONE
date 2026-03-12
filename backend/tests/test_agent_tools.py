"""
Agent 工具定义单元测试

覆盖：validate_tool_call、工具分类集合、AGENT_TOOLS 结构
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config.agent_tools import (
    ALL_TOOLS,
    AGENT_TOOLS,
    INFO_TOOLS,
    ROUTING_TOOLS,
    TOOL_SCHEMAS,
    validate_tool_call,
)


# ============================================================
# TestValidateToolCall
# ============================================================


class TestValidateToolCall:

    def test_unknown_tool_rejected(self):
        """未知工具名→False"""
        assert validate_tool_call("hallucinated_tool", {}) is False

    def test_empty_tool_name_rejected(self):
        """空工具名→False"""
        assert validate_tool_call("", {}) is False

    def test_valid_info_tool_no_required(self):
        """get_conversation_context 无必填字段→True"""
        assert validate_tool_call("get_conversation_context", {}) is True

    def test_valid_info_tool_with_optional(self):
        """get_conversation_context 带可选参数→True"""
        assert validate_tool_call(
            "get_conversation_context", {"limit": 5},
        ) is True

    def test_web_search_with_required(self):
        """web_search 有必填参数→True"""
        assert validate_tool_call(
            "web_search", {"search_query": "天气"},
        ) is True

    def test_web_search_missing_required(self):
        """web_search 缺少必填参数→False"""
        assert validate_tool_call("web_search", {}) is False

    def test_search_knowledge_with_required(self):
        """search_knowledge 有必填参数→True"""
        assert validate_tool_call(
            "search_knowledge", {"query": "模型表现"},
        ) is True

    def test_search_knowledge_missing_required(self):
        """search_knowledge 缺必填→False"""
        assert validate_tool_call("search_knowledge", {}) is False

    def test_route_to_chat_valid(self):
        """route_to_chat 必填齐全→True"""
        assert validate_tool_call("route_to_chat", {
            "system_prompt": "你是翻译", "model": "gemini-3-pro",
        }) is True

    def test_route_to_chat_missing_model(self):
        """route_to_chat 缺 model→False"""
        assert validate_tool_call(
            "route_to_chat", {"system_prompt": "你是翻译"},
        ) is False

    def test_route_to_chat_missing_system_prompt(self):
        """route_to_chat 缺 system_prompt→False"""
        assert validate_tool_call(
            "route_to_chat", {"model": "gemini-3-pro"},
        ) is False

    def test_route_to_image_valid(self):
        """route_to_image 必填齐全→True"""
        assert validate_tool_call("route_to_image", {
            "prompts": [{"prompt": "cat"}], "model": "flux",
        }) is True

    def test_route_to_image_missing_prompts(self):
        """route_to_image 缺 prompts→False"""
        assert validate_tool_call(
            "route_to_image", {"model": "flux"},
        ) is False

    def test_route_to_video_valid(self):
        """route_to_video 必填齐全→True"""
        assert validate_tool_call("route_to_video", {
            "prompt": "waves on beach", "model": "vidu",
        }) is True

    def test_route_to_video_missing_prompt(self):
        """route_to_video 缺 prompt→False"""
        assert validate_tool_call(
            "route_to_video", {"model": "vidu"},
        ) is False

    def test_ask_user_valid(self):
        """ask_user 必填齐全→True"""
        assert validate_tool_call("ask_user", {
            "message": "你想要什么？", "reason": "need_info",
        }) is True

    def test_ask_user_missing_reason(self):
        """ask_user 缺 reason→False"""
        assert validate_tool_call(
            "ask_user", {"message": "hello"},
        ) is False

    def test_erp_tool_valid(self):
        """ERP 工具必填齐全→True"""
        assert validate_tool_call(
            "erp_trade_query", {"action": "order_list"},
        ) is True

    def test_erp_tool_missing_required(self):
        """ERP 工具缺必填→False"""
        assert validate_tool_call("erp_trade_query", {}) is False


# ============================================================
# TestToolSets — 工具分类验证
# ============================================================


class TestToolSets:

    def test_info_and_routing_no_overlap(self):
        """INFO 和 ROUTING 工具不重叠"""
        assert INFO_TOOLS & ROUTING_TOOLS == set()

    def test_all_tools_equals_union(self):
        """ALL_TOOLS = INFO + ROUTING"""
        assert ALL_TOOLS == INFO_TOOLS | ROUTING_TOOLS

    def test_routing_tools_expected(self):
        """ROUTING 工具包含 4 个核心工具"""
        expected = {"route_to_chat", "route_to_image", "route_to_video", "ask_user"}
        assert expected <= ROUTING_TOOLS

    def test_info_tools_has_core(self):
        """INFO 工具包含 3 个核心工具"""
        expected = {"web_search", "get_conversation_context", "search_knowledge"}
        assert expected <= INFO_TOOLS

    def test_all_tools_have_schemas(self):
        """所有工具都有 schema 定义"""
        for tool in ALL_TOOLS:
            assert tool in TOOL_SCHEMAS, f"Missing schema for {tool}"


# ============================================================
# TestAgentToolsStructure — AGENT_TOOLS 结构验证
# ============================================================


class TestAgentToolsStructure:

    def test_tools_are_list(self):
        """AGENT_TOOLS 是列表"""
        assert isinstance(AGENT_TOOLS, list)

    def test_tools_not_empty(self):
        """AGENT_TOOLS 非空"""
        assert len(AGENT_TOOLS) > 0

    def test_each_tool_has_function(self):
        """每个工具有 type=function 和 function 字段"""
        for tool in AGENT_TOOLS:
            assert tool["type"] == "function"
            assert "function" in tool
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_tool_names_match_all_tools(self):
        """AGENT_TOOLS 中的工具名与 ALL_TOOLS 一致"""
        tool_names = {t["function"]["name"] for t in AGENT_TOOLS}
        assert tool_names == ALL_TOOLS


# ============================================================
# TestBuildErpTools — ERP 工具构建
# ============================================================


class TestBuildErpTools:

    def test_returns_8_tools(self):
        """build_erp_tools 返回 8 个工具（6 ERP查询 + 1 淘宝奇门 + 1 写入）"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        assert len(tools) == 8

    def test_each_tool_structure(self):
        """每个工具有完整的 function calling 结构"""
        from config.erp_tools import build_erp_tools
        for tool in build_erp_tools():
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            assert func["parameters"]["type"] == "object"

    def test_query_tools_have_action_enum(self):
        """6 个查询工具都有 action enum"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        query_tools = [t for t in tools
                       if t["function"]["name"] != "erp_execute"]
        for tool in query_tools:
            props = tool["function"]["parameters"]["properties"]
            assert "action" in props
            assert "enum" in props["action"]
            assert len(props["action"]["enum"]) > 0

    def test_execute_tool_has_category(self):
        """erp_execute 工具有 category 参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        execute = [t for t in tools
                   if t["function"]["name"] == "erp_execute"][0]
        props = execute["function"]["parameters"]["properties"]
        assert "category" in props
        assert "action" in props

    def test_read_actions_excludes_writes(self):
        """_read_actions 只返回读操作"""
        from config.erp_tools import _read_actions
        from services.kuaimai.registry.base import ApiEntry
        registry = {
            "list": ApiEntry(method="m", description="查询列表"),
            "add": ApiEntry(method="m", description="新增", is_write=True),
        }
        actions, desc = _read_actions(registry)
        assert "list" in actions
        assert "add" not in actions

    def test_write_actions_by_category(self):
        """_write_actions_by_category 包含写操作描述"""
        from config.erp_tools import _write_actions_by_category
        result = _write_actions_by_category()
        assert isinstance(result, str)

    def test_erp_tool_schemas(self):
        """ERP_TOOL_SCHEMAS 覆盖所有 ERP 工具"""
        from config.erp_tools import ERP_SYNC_TOOLS, ERP_TOOL_SCHEMAS
        for tool in ERP_SYNC_TOOLS:
            assert tool in ERP_TOOL_SCHEMAS

    def test_trade_query_has_time_type_param(self):
        """erp_trade_query 包含 time_type 参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        trade = [t for t in tools
                 if t["function"]["name"] == "erp_trade_query"][0]
        props = trade["function"]["parameters"]["properties"]
        assert "time_type" in props
        assert "created" in props["time_type"]["description"]
        assert "pay_time" in props["time_type"]["description"]

    def test_trade_query_has_shop_name_param(self):
        """erp_trade_query 包含 shop_name 参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        trade = [t for t in tools
                 if t["function"]["name"] == "erp_trade_query"][0]
        props = trade["function"]["parameters"]["properties"]
        assert "shop_name" in props

    def test_trade_query_status_has_enum_values(self):
        """erp_trade_query status 描述包含具体枚举值"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        trade = [t for t in tools
                 if t["function"]["name"] == "erp_trade_query"][0]
        status_desc = trade["function"]["parameters"]["properties"]["status"]["description"]
        assert "WAIT_AUDIT" in status_desc
        assert "SELLER_SEND_GOODS" in status_desc


class TestErpTaobaoQueryTool:

    def test_taobao_query_has_status_enum(self):
        """erp_taobao_query 包含 status 参数及淘宝状态枚举"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        props = taobao["function"]["parameters"]["properties"]
        assert "status" in props
        assert "WAIT_BUYER_PAY" in props["status"]["description"]
        assert "SELLER_SEND_GOODS" in props["status"]["description"]

    def test_taobao_query_has_date_type(self):
        """erp_taobao_query 包含 date_type（integer 类型）"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        props = taobao["function"]["parameters"]["properties"]
        assert "date_type" in props
        assert props["date_type"]["type"] == "integer"

    def test_taobao_query_has_shop_id(self):
        """erp_taobao_query 包含 shop_id 参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        props = taobao["function"]["parameters"]["properties"]
        assert "shop_id" in props

    def test_taobao_query_has_page_size(self):
        """erp_taobao_query 包含 page_size 参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        props = taobao["function"]["parameters"]["properties"]
        assert "page_size" in props

    def test_taobao_query_has_refund_type(self):
        """erp_taobao_query 包含 refund_type 参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        props = taobao["function"]["parameters"]["properties"]
        assert "refund_type" in props


class TestErpRoutingPrompt:

    def test_multistep_strategy_present(self):
        """ERP_ROUTING_PROMPT 包含多步查询策略"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "多步查询策略" in ERP_ROUTING_PROMPT

    def test_no_immediate_route_to_chat(self):
        """ERP_ROUTING_PROMPT 不再要求查询后立即 route_to_chat"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "ERP查询结果返回后，用 route_to_chat 总结回复用户" not in ERP_ROUTING_PROMPT

    def test_encourages_pagination(self):
        """ERP_ROUTING_PROMPT 鼓励翻页"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "翻页" in ERP_ROUTING_PROMPT

    def test_mentions_time_type(self):
        """ERP_ROUTING_PROMPT 提到 time_type 用法"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "time_type" in ERP_ROUTING_PROMPT
