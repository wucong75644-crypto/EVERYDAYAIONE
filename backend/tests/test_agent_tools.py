"""
Agent 工具定义单元测试

覆盖：validate_tool_call、工具分类集合、ERP 工具构建、路由提示词
v1 AGENT_TOOLS/AGENT_SYSTEM_PROMPT 已移除，相关测试已清理。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config.agent_tools import (
    ALL_TOOLS,
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

    def test_route_to_chat_without_model_valid(self):
        """route_to_chat 无 model→True（v2 Phase2 由 _inject_phase1_model 注入）"""
        assert validate_tool_call(
            "route_to_chat", {"system_prompt": "你是翻译"},
        ) is True

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
        """INFO 工具包含核心工具"""
        expected = {"get_conversation_context", "search_knowledge"}
        assert expected <= INFO_TOOLS

    def test_all_tools_have_schemas(self):
        """所有工具都有 schema 定义"""
        for tool in ALL_TOOLS:
            assert tool in TOOL_SCHEMAS, f"Missing schema for {tool}"


# ============================================================
# TestBuildErpTools — ERP 工具构建
# ============================================================


class TestBuildErpTools:

    def test_returns_19_tools(self):
        """build_erp_tools 返回 20 个工具（8 API + 12 本地）"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        assert len(tools) == 20

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
        """7 个 API 查询工具都有 action enum"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from config.erp_tools import build_erp_tools
        skip = {"erp_execute"} | ERP_LOCAL_TOOLS
        tools = build_erp_tools()
        query_tools = [t for t in tools
                       if t["function"]["name"] not in skip]
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

    def test_query_tools_have_params_object(self):
        """API 查询工具使用 params: object（两步调用模式）"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        skip = {"erp_execute"} | ERP_LOCAL_TOOLS
        query_tools = [t for t in tools
                       if t["function"]["name"] not in skip]
        for tool in query_tools:
            props = tool["function"]["parameters"]["properties"]
            assert "params" in props, (
                f"{tool['function']['name']} 缺少 params"
            )
            assert props["params"]["type"] == "object"

    def test_query_tools_no_flat_params(self):
        """API 查询工具不含扁平化的业务参数（已迁移到 params object）"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        flat_params = {
            "shop_name", "shop_ids", "order_types", "time_type",
            "buyer", "order_id", "system_id",
            "sku_outer_id", "shop_id", "refund_type", "date_type",
        }
        skip = {"erp_execute"} | ERP_LOCAL_TOOLS
        for tool in tools:
            if tool["function"]["name"] in skip:
                continue
            props = tool["function"]["parameters"]["properties"]
            found = flat_params & set(props.keys())
            assert not found, (
                f"{tool['function']['name']} 仍有扁平参数: {found}"
            )


class TestErpTaobaoQueryTool:

    def test_taobao_query_has_params_object(self):
        """erp_taobao_query 使用 params: object（两步调用模式）"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        props = taobao["function"]["parameters"]["properties"]
        assert "params" in props
        assert props["params"]["type"] == "object"

    def test_taobao_query_has_page_size(self):
        """erp_taobao_query 包含 page_size 参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        props = taobao["function"]["parameters"]["properties"]
        assert "page_size" in props

    def test_taobao_query_action_desc_has_params(self):
        """erp_taobao_query action 描述包含参数信息"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        taobao = [t for t in tools
                  if t["function"]["name"] == "erp_taobao_query"][0]
        action_desc = taobao["function"]["parameters"][
            "properties"]["action"]["description"]
        assert "status" in action_desc
        assert "date_type" in action_desc


class TestErpRoutingPrompt:

    def test_multistep_strategy_present(self):
        """ERP_ROUTING_PROMPT 包含两步查询策略"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "两步查询" in ERP_ROUTING_PROMPT

    def test_no_immediate_route_to_chat(self):
        """ERP_ROUTING_PROMPT 不再要求查询后立即 route_to_chat"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "ERP查询结果返回后，用 route_to_chat 总结回复用户" not in ERP_ROUTING_PROMPT

    def test_mentions_time_type(self):
        """ERP_ROUTING_PROMPT 提到 time_type 用法"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "time_type" in ERP_ROUTING_PROMPT

    def test_p0_aftersales_cross_tool(self):
        """P0: 售后查询跨工具的决策"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        for keyword in [
            "aftersale_list", "refund_list", "refund_warehouse",
        ]:
            assert keyword in ERP_ROUTING_PROMPT, f"Missing aftersales: {keyword}"

    def test_p0_archive_difference(self):
        """P0: 归档差异（订单 query_type vs 采购换 action）"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "query_type=1" in ERP_ROUTING_PROMPT
        assert "purchase_order_history" in ERP_ROUTING_PROMPT or "_history" in ERP_ROUTING_PROMPT

    def test_p0_trade_vs_taobao_query(self):
        """P0: erp_taobao_query 时间参数 date_type 在提示词中"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "date_type" in ERP_ROUTING_PROMPT
        assert "erp_taobao_query" in ERP_ROUTING_PROMPT

    def test_p0_required_params_trap(self):
        """P0: 必填参数陷阱提示"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "refund_warehouse" in ERP_ROUTING_PROMPT
        assert "time_type" in ERP_ROUTING_PROMPT

    def test_p1_product_query_in_scenario_docs(self):
        """P1: 商品查询 action 选择在场景指南中"""
        from services.kuaimai.api_search import _SCENARIO_DOCS
        doc = _SCENARIO_DOCS.get("商品查询", "")
        for action in [
            "product_list", "product_detail", "multi_product",
            "sku_list", "multicode_query", "item_supplier_list",
        ]:
            assert action in doc, f"Missing product action: {action}"

    def test_p1_purchase_chain_in_scenario_docs(self):
        """P1: 采购链路 4 阶段在场景指南中"""
        from services.kuaimai.api_search import _SCENARIO_DOCS
        doc = _SCENARIO_DOCS.get("采购", "")
        for action in [
            "purchase_order_list", "warehouse_entry_list",
            "shelf_list", "purchase_return_list", "purchase_strategy",
        ]:
            assert action in doc, f"Missing purchase action: {action}"

    def test_p1_relay_keys(self):
        """P1: 中继键 sid/order_no/outer_id 在提示词中"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "sid" in ERP_ROUTING_PROMPT
        assert "order_no" in ERP_ROUTING_PROMPT
        assert "outer_id" in ERP_ROUTING_PROMPT

    def test_p2_statistics_in_scenario_docs(self):
        """P2: 统计类汇总策略在场景指南中"""
        from services.kuaimai.api_search import _SCENARIO_DOCS
        doc = _SCENARIO_DOCS.get("统计", "")
        assert "退货率" in doc
        assert "各仓库库存" in doc

    def test_p2_fallback_strategy(self):
        """P2: 查不到时的降级策略"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "查不到" in ERP_ROUTING_PROMPT
        assert "ask_user" in ERP_ROUTING_PROMPT

    def test_broadened_code_query_documented(self):
        """编码智能匹配功能在提示词中有说明"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "基础编码" in ERP_ROUTING_PROMPT
        assert "无需手动重试" in ERP_ROUTING_PROMPT

    def test_tool_capability_section(self):
        """ERP_ROUTING_PROMPT 包含工具能力说明（能力驱动架构）"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "local_*" in ERP_ROUTING_PROMPT
        assert "erp_*" in ERP_ROUTING_PROMPT
        assert "毫秒级" in ERP_ROUTING_PROMPT
        assert "远程ERP API" in ERP_ROUTING_PROMPT

    def test_data_freshness_section(self):
        """ERP_ROUTING_PROMPT 包含数据新鲜度和同步警告降级规则"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "同步警告" in ERP_ROUTING_PROMPT
        assert "trigger_erp_sync" in ERP_ROUTING_PROMPT

    def test_no_routing_directives(self):
        """ERP_ROUTING_PROMPT 不包含链路指令"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "简单统计" not in ERP_ROUTING_PROMPT
        assert "→ code_execute" not in ERP_ROUTING_PROMPT

    def test_remote_tools_labeled(self):
        """远程 ERP 工具描述包含"远程API"标识"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        remote_tools = [
            t for t in tools
            if t["function"]["name"].startswith("erp_")
            and t["function"]["name"] != "erp_execute"
        ]
        for tool in remote_tools:
            desc = tool["function"]["description"]
            assert "远程API" in desc, (
                f"{tool['function']['name']} 缺少远程API标识"
            )


# ============================================================
# TestToolRegistry — 新注册表验证
# ============================================================


class TestToolRegistry:

    def test_registry_has_all_erp_tools(self):
        """注册表包含所有 ERP 工具"""
        from config.tool_registry import TOOL_REGISTRY
        from config.erp_tools import ERP_SYNC_TOOLS
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        for tool in ERP_SYNC_TOOLS | ERP_LOCAL_TOOLS:
            assert tool in TOOL_REGISTRY, f"Missing: {tool}"

    def test_registry_has_always_include(self):
        """注册表有 6 个常驻工具"""
        from config.tool_registry import TOOL_REGISTRY
        always = [t for t in TOOL_REGISTRY.values() if t.always_include]
        assert len(always) == 6

    def test_local_tools_priority_1(self):
        """本地工具 priority=1"""
        from config.tool_registry import TOOL_REGISTRY
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        for name in ERP_LOCAL_TOOLS:
            entry = TOOL_REGISTRY.get(name)
            if entry:
                assert entry.priority == 1, f"{name} priority={entry.priority}"

    def test_synonym_expansion(self):
        """同义词扩展正常工作"""
        from config.tool_registry import expand_synonyms
        result = expand_synonyms("库存卖了多少")
        assert "销量" in result
        assert "订单" in result
        assert "统计" in result


# ============================================================
# BASE_AGENT_PROMPT 退出规则
# ============================================================


class TestBaseAgentPrompt:
    """BASE_AGENT_PROMPT 关键规则验证"""

    def test_prompt_requires_tool_before_answer(self):
        """提示词要求必须先调工具再总结"""
        from config.phase_tools import BASE_AGENT_PROMPT
        assert "禁止直接回答" in BASE_AGENT_PROMPT
        assert "通过工具获取数据" in BASE_AGENT_PROMPT

    def test_prompt_allows_text_output(self):
        """提示词允许直接用文字总结结论"""
        from config.phase_tools import BASE_AGENT_PROMPT
        assert "直接用文字总结结论" in BASE_AGENT_PROMPT

    def test_prompt_route_to_chat_optional(self):
        """提示词表明 route_to_chat 是可选的"""
        from config.phase_tools import BASE_AGENT_PROMPT
        assert "不需要调 route_to_chat" in BASE_AGENT_PROMPT

    def test_prompt_no_contradiction(self):
        """提示词不应包含"必须通过工具退出循环"（旧规则已删）"""
        from config.phase_tools import BASE_AGENT_PROMPT
        assert "必须通过工具退出循环" not in BASE_AGENT_PROMPT
