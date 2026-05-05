"""
工具域隔离层（Tool Domain Isolation）

每个工具注册所属域，Agent 加载和搜索工具时按域过滤。
子 Agent 的内部工具对主 Agent 物理不可见，从架构上阻断泄漏路径。

设计参考：
- Anthropic Agent SDK: 工具绑定到 Agent 实例
- LangGraph: Agent 节点绑定专用工具集
- 本项目: 在现有全局工具池上叠加域过滤层

三个拦截点：
1. get_core_tools → 初始加载时按域过滤
2. extract_tool_names_from_result → ToolSearch 结果按域过滤
3. 动态注入时 → filter_tools_for_domain 二次过滤
"""

from enum import Enum
from typing import Any, Dict, List, Set


class ToolDomain(str, Enum):
    """工具所属域"""
    GENERAL = "general"   # 所有 Agent 可用（搜索/生成/文件等）
    ERP = "erp"           # 仅 erp_agent 内部可用
    SHARED = "shared"     # 跨域共享（如 code_execute，多个 Agent 都需要）


# ============================================================
# 工具域注册表
#
# 所有工具必须在此注册，未注册的工具默认拒绝访问。
# 新增工具时在此添加一行即可，validate_registry 会在启动时检查遗漏。
# ============================================================

TOOL_DOMAINS: Dict[str, ToolDomain] = {
    # === general: 主 Agent 可直接使用 ===
    "erp_agent":        ToolDomain.GENERAL,
    "erp_analyze":      ToolDomain.GENERAL,
    "search_knowledge": ToolDomain.GENERAL,
    "web_search":       ToolDomain.GENERAL,
    "social_crawler":   ToolDomain.GENERAL,
    "generate_image":   ToolDomain.GENERAL,
    "generate_video":   ToolDomain.GENERAL,
    "image_agent":      ToolDomain.GENERAL,
    "file_read":        ToolDomain.GENERAL,
    "file_list":        ToolDomain.GENERAL,
    "file_search":      ToolDomain.GENERAL,
    "manage_scheduled_task": ToolDomain.GENERAL,

    # === shared: 多个域的 Agent 内部都能用 ===
    "code_execute":     ToolDomain.SHARED,
    "data_query":       ToolDomain.SHARED,  # 主 Agent + ERP Agent 都需要查询数据文件
    "ask_user":         ToolDomain.SHARED,  # 主 Agent + ERP Agent 都需要追问能力

    # === erp: 仅 erp_agent 内部可用 ===
    "erp_api_search":           ToolDomain.ERP,
    "erp_info_query":           ToolDomain.ERP,
    "erp_product_query":        ToolDomain.ERP,
    "erp_trade_query":          ToolDomain.ERP,
    "erp_aftersales_query":     ToolDomain.ERP,
    "erp_warehouse_query":      ToolDomain.ERP,
    "erp_purchase_query":       ToolDomain.ERP,
    "erp_taobao_query":         ToolDomain.ERP,
    "erp_execute":              ToolDomain.ERP,
    "local_data":               ToolDomain.ERP,  # 统一查询引擎
    "local_product_stats":      ToolDomain.ERP,
    "local_stock_query":        ToolDomain.ERP,
    "local_product_identify":   ToolDomain.ERP,
    "local_platform_map_query": ToolDomain.ERP,
    "local_compare_stats":      ToolDomain.ERP,
    "local_shop_list":          ToolDomain.ERP,
    "local_warehouse_list":     ToolDomain.ERP,
    "local_supplier_list":      ToolDomain.ERP,
    "fetch_all_pages":          ToolDomain.ERP,
    "trigger_erp_sync":         ToolDomain.ERP,
    "route_to_chat":            ToolDomain.ERP,
}


# ============================================================
# 域感知过滤函数
# ============================================================


def can_access(tool_name: str, agent_domain: str) -> bool:
    """判断指定域的 Agent 是否有权使用该工具

    规则：
    - SHARED 域工具：所有 Agent 可用
    - GENERAL 域工具：仅 agent_domain="general" 可用
    - ERP 域工具：仅 agent_domain="erp" 可用
    - 未注册工具：拒绝（保守策略）
    """
    domain = TOOL_DOMAINS.get(tool_name)
    if domain is None:
        return False
    if domain == ToolDomain.SHARED:
        return True
    return domain.value == agent_domain


def filter_tools_for_domain(
    tools: List[Dict[str, Any]], agent_domain: str,
) -> List[Dict[str, Any]]:
    """过滤工具列表，只返回指定域可访问的工具"""
    return [
        t for t in tools
        if can_access(t["function"]["name"], agent_domain)
    ]


def validate_registry(all_tool_names: Set[str]) -> List[str]:
    """启动时校验：所有工具必须注册域

    返回未注册的工具名列表。调用方应 log warning。
    """
    return sorted(n for n in all_tool_names if n not in TOOL_DOMAINS)
