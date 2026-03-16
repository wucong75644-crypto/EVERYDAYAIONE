"""
ERP API 搜索服务

提供按需发现 ERP API 操作和参数文档的能力。
支持两种搜索模式：
- 精确查询：tool:action 格式（如 erp_trade_query:order_list）
- 关键词匹配：自然语言搜索（如 "退款" "库存"）

搜索范围：TOOL_REGISTRIES 中所有注册的 action + ApiEntry 元数据。
"""

from typing import List, Tuple

from services.kuaimai.registry import TOOL_REGISTRIES
from services.kuaimai.registry.base import ApiEntry

# 搜索结果最大条数
_MAX_RESULTS = 5


def search_erp_api(query: str) -> str:
    """搜索 ERP 可用的 API 操作和参数文档

    Args:
        query: 自然语言关键词或 'tool:action' 精确查询

    Returns:
        格式化的 API 文档文本
    """
    query = query.strip()
    if not query:
        return "请输入搜索关键词"

    # 精确查询模式：tool:action
    if ":" in query:
        return _exact_search(query)

    # 关键词搜索模式
    return _keyword_search(query)


def _exact_search(query: str) -> str:
    """精确查询：tool_name:action_name"""
    parts = query.split(":", 1)
    tool_name = parts[0].strip()
    action_name = parts[1].strip() if len(parts) > 1 else ""

    registry = TOOL_REGISTRIES.get(tool_name)
    if not registry:
        available_tools = ", ".join(sorted(TOOL_REGISTRIES.keys()))
        return f"未找到工具「{tool_name}」，可用工具: {available_tools}"

    if action_name:
        entry = registry.get(action_name)
        if not entry:
            available = ", ".join(sorted(registry.keys()))
            return (
                f"工具 {tool_name} 无操作「{action_name}」，"
                f"可用操作: {available}"
            )
        return _format_entry_detail(tool_name, action_name, entry)

    # 只指定了 tool_name，列出所有 action
    return _format_tool_actions(tool_name, registry)


def _keyword_search(query: str) -> str:
    """关键词搜索：在 action 名称和描述中匹配"""
    keywords = query.lower().split()
    matches: List[Tuple[int, str, str, ApiEntry]] = []

    for tool_name, registry in TOOL_REGISTRIES.items():
        if not isinstance(registry, dict):
            continue
        for action_name, entry in registry.items():
            if not isinstance(entry, ApiEntry):
                continue
            score = _calc_match_score(
                keywords, tool_name, action_name, entry,
            )
            if score > 0:
                matches.append((score, tool_name, action_name, entry))

    if not matches:
        return f"未找到与「{query}」匹配的 ERP API 操作，请尝试其他关键词"

    # 按匹配度降序排序，取前 N 条
    matches.sort(key=lambda x: x[0], reverse=True)
    top = matches[:_MAX_RESULTS]

    lines = [f"找到 {len(matches)} 个匹配，显示前 {len(top)} 个：\n"]
    for _, tool_name, action_name, entry in top:
        lines.append(
            _format_entry_brief(tool_name, action_name, entry)
        )
    return "\n".join(lines)


def _calc_match_score(
    keywords: List[str],
    tool_name: str,
    action_name: str,
    entry: ApiEntry,
) -> int:
    """计算关键词匹配分数（越高越匹配）"""
    score = 0
    search_text = (
        f"{tool_name} {action_name} {entry.description} "
        f"{' '.join(entry.param_map.keys())}"
    ).lower()

    for kw in keywords:
        if kw in action_name.lower():
            score += 3  # action 名称匹配权重最高
        elif kw in entry.description:
            score += 2  # 描述匹配
        elif kw in search_text:
            score += 1  # 参数名等其他匹配
    return score


def _format_entry_detail(
    tool_name: str, action_name: str, entry: ApiEntry,
) -> str:
    """格式化单个 API 操作的完整文档"""
    params = entry.param_map
    required = set(entry.required_params)

    lines = [
        f"📋 {tool_name}:{action_name}",
        f"描述: {entry.description}",
        f"API方法: {entry.method}",
    ]

    if params:
        param_lines = []
        for user_key, api_key in params.items():
            marker = "（必填）" if user_key in required else ""
            doc = entry.param_docs.get(user_key, "")
            doc_str = f": {doc}" if doc else ""
            param_lines.append(
                f"  - {user_key}{marker}{doc_str} → {api_key}"
            )
        lines.append("参数:")
        lines.extend(param_lines)
    else:
        lines.append("参数: 无（仅需指定 action）")

    if entry.defaults:
        lines.append(f"默认值: {entry.defaults}")

    if entry.is_write:
        lines.append("类型: 写操作（需用户确认）")

    if entry.error_codes:
        lines.append("错误码:")
        for code, desc in entry.error_codes.items():
            lines.append(f"  - {code}: {desc}")

    return "\n".join(lines)


def _format_entry_brief(
    tool_name: str, action_name: str, entry: ApiEntry,
) -> str:
    """格式化 API 操作的简要信息"""
    params = list(entry.param_map.keys())
    required = set(entry.required_params)
    param_parts = [
        f"*{p}" if p in required else p for p in params
    ]
    param_str = f"({'/'.join(param_parts)})" if param_parts else ""
    return f"- {tool_name}:{action_name} — {entry.description}{param_str}"


def _format_tool_actions(
    tool_name: str, registry: dict,
) -> str:
    """列出工具的所有操作"""
    lines = [f"工具 {tool_name} 的所有操作：\n"]
    for action_name, entry in sorted(registry.items()):
        if not isinstance(entry, ApiEntry):
            continue
        lines.append(
            _format_entry_brief(tool_name, action_name, entry)
        )
    return "\n".join(lines)
