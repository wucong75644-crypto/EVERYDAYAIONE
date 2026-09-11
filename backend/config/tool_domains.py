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

from services.tools.catalog import definition_registry

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
# 运行工具从 ToolSpec 派生；route_to_chat 仅为旧 Phase 出口分类。
# 新增运行工具只在 services/tools/definitions 注册。
# ============================================================

TOOL_DOMAINS = {"route_to_chat": ToolDomain.ERP} | {s.name: ToolDomain(s.domain) for s in definition_registry().specs() if s.name != "get_conversation_context"}


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
    # Keep the two legacy classification exceptions; neither can add a runtime
    # handler or bypass Registry exposure checks.
    if tool_name == "route_to_chat":
        return agent_domain == "erp"
    spec = definition_registry().get(tool_name)
    domain = ToolDomain(spec.domain) if spec is not None and tool_name != "get_conversation_context" else None
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
    catalog_names = {spec.name for spec in definition_registry().specs()
                     if spec.name != "get_conversation_context"} | {"route_to_chat"}
    return sorted(all_tool_names - catalog_names)
