"""
Agent 工具定义

工具分类、Schema 验证、路由工具定义。
v2 架构：Phase 1 意图路由 + Phase 2 动态工具加载。
"""

from typing import Any, Dict, List, Set

from config.code_tools import (
    CODE_INFO_TOOLS,
    CODE_TOOL_SCHEMAS,
    build_code_tools,
)
from config.file_tools import (
    FILE_INFO_TOOLS,
    FILE_TOOL_SCHEMAS,
    build_file_tools,
)
from config.crawler_tools import (
    CRAWLER_INFO_TOOLS,
    CRAWLER_TOOL_SCHEMAS,
    build_crawler_tools,
)
from config.erp_local_tools import ERP_LOCAL_TOOLS
from config.erp_tools import (
    ERP_SYNC_TOOLS,
    ERP_TOOL_SCHEMAS,
    build_erp_tools,
    build_erp_search_tool,
)
from config.smart_model_config import (
    SMART_CONFIG,
    _get_model_enum,
    _get_model_desc,
)


# ============================================================
# 工具分类（2 类：信息采集 + 路由决策）
# ============================================================

INFO_TOOLS: Set[str] = {
    "get_conversation_context", "search_knowledge",
    "erp_api_search",
} | ERP_SYNC_TOOLS | ERP_LOCAL_TOOLS | CRAWLER_INFO_TOOLS | CODE_INFO_TOOLS | FILE_INFO_TOOLS

ROUTING_TOOLS: Set[str] = {
    "route_to_chat", "route_to_image", "route_to_video", "ask_user",
}

ALL_TOOLS: Set[str] = INFO_TOOLS | ROUTING_TOOLS

# 向后兼容别名（test_kuaimai.py 使用 SYNC_TOOLS）
SYNC_TOOLS = INFO_TOOLS


# ============================================================
# 工具 Schema（用于验证，防止幻觉调用）
# ============================================================

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    # === 信息工具 ===
    "get_conversation_context": {
        "required": [],
        "properties": {"limit": {"type": "integer"}},
    },
    "search_knowledge": {
        "required": ["query"],
        "properties": {"query": {"type": "string"}},
    },
    # === 搜索工具 ===
    "erp_api_search": {
        "required": ["query"],
        "properties": {"query": {"type": "string"}},
    },
    # === 路由工具 ===
    "route_to_chat": {
        "required": ["system_prompt"],
        "properties": {
            "system_prompt": {"type": "string"},
            "model": {"type": "string"},
            "needs_google_search": {"type": "boolean"},
        },
    },
    "route_to_image": {
        "required": ["prompts"],
        "properties": {
            "prompts": {"type": "array"},
            "model": {"type": "string"},
        },
    },
    "route_to_video": {
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string"},
        },
    },
    "ask_user": {
        "required": ["message", "reason"],
        "properties": {
            "message": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
    # === ERP 工具 ===
    **ERP_TOOL_SCHEMAS,
    # === 爬虫工具 ===
    **CRAWLER_TOOL_SCHEMAS,
    # === 代码执行工具 ===
    **CODE_TOOL_SCHEMAS,
    # === 文件操作工具 ===
    **FILE_TOOL_SCHEMAS,
}


def validate_tool_call(tool_name: str, arguments: Dict[str, Any]) -> bool:
    """验证工具调用参数（防止幻觉工具名和缺失必填字段）"""
    if tool_name not in ALL_TOOLS:
        return False
    schema = TOOL_SCHEMAS.get(tool_name)
    if not schema:
        return True
    for req_field in schema.get("required", []):
        if req_field not in arguments:
            return False
    return True


# ============================================================
# 工具定义构建
# ============================================================



# v1 build_agent_tools() 和 build_agent_system_prompt() 已移除
# v2 使用 Phase 1 (phase_tools.py) + Phase 2 (build_domain_tools) 动态加载
