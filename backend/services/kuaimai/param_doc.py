"""
参数文档生成器

两步调用模式的 Step 1：
LLM 只传 action → 本模块从 ApiEntry.param_docs 生成该 action 的参数文档 → 返回给 LLM。
LLM 按文档在 Step 2 传入正确参数。

generate_param_hints：
工具执行结果附带精简参数提示，帮助 AI 校验已用参数、发现可用参数。
"""

from typing import Dict, Optional, Set

from services.kuaimai.registry import TOOL_REGISTRIES
from services.kuaimai.registry.base import ApiEntry

# 不需要提示的通用/低价值参数
_SKIP_PARAMS: Set[str] = {"page", "page_size", "pageNo", "pageSize"}


def generate_param_doc(tool_name: str, action: str) -> str:
    """生成 action 级别的参数文档

    Args:
        tool_name: 工具名（如 erp_trade_query）
        action: 操作名（如 order_list）

    Returns:
        格式化的参数文档文本
    """
    registry = TOOL_REGISTRIES.get(tool_name)
    if not registry:
        available = ", ".join(sorted(TOOL_REGISTRIES.keys()))
        return f"未知工具「{tool_name}」，可用工具: {available}"

    entry: Optional[ApiEntry] = registry.get(action)
    if not entry:
        available = ", ".join(sorted(registry.keys()))
        return f"未知操作「{action}」，可选: {available}"

    return _format_param_doc(action, entry)


def _format_param_doc(action: str, entry: ApiEntry) -> str:
    """格式化单个 action 的参数文档"""
    required = set(entry.required_params)
    lines = [f"📋 {action} — {entry.description}\n"]

    if required:
        lines.append(f"必填参数: {', '.join(required)}\n")

    if entry.param_map:
        lines.append("参数:")
        for user_key in entry.param_map:
            marker = "（必填）" if user_key in required else ""
            doc = entry.param_docs.get(user_key, "")
            lines.append(f"  - {user_key}{marker}: {doc}")
            hint = entry.param_hints.get(user_key)
            if hint:
                lines.append(f"    ⚠ {hint}")
    else:
        lines.append("参数: 无（仅需指定 action）")

    if entry.defaults:
        defaults_str = ", ".join(f"{k}={v}" for k, v in entry.defaults.items())
        lines.append(f"\n默认值: {defaults_str}")

    lines.append("\n通用参数:")
    lines.append("  - page: 页码（默认1）")
    lines.append("  - page_size: 每页条数（默认20，最小20）")

    if entry.error_codes:
        lines.append("\n错误码:")
        for code, desc in entry.error_codes.items():
            lines.append(f"  - {code}: {desc}")

    lines.append("\n请在 params 中传入所需参数后再次调用。")
    return "\n".join(lines)


def generate_param_hints(
    tool_name: str, action: str, used_params: Dict[str, object],
) -> str:
    """生成精简的参数提示（附在工具执行结果后面）

    根据 AI 已传的参数，按需返回：
    - 已传参数的校验确认
    - 未传但常用的参数提示
    不返回分页等低价值参数，控制 token 消耗。

    Args:
        tool_name: 工具名
        action: 操作名
        used_params: AI 本次传入的参数字典

    Returns:
        精简提示文本，无匹配时返回空字符串
    """
    registry = TOOL_REGISTRIES.get(tool_name)
    if not registry:
        return ""
    entry: Optional[ApiEntry] = registry.get(action)
    if not entry or not entry.param_map:
        return ""

    used_keys = set(used_params.keys()) - _SKIP_PARAMS
    all_keys = set(entry.param_map.keys()) - _SKIP_PARAMS
    unused_keys = all_keys - used_keys
    required = set(entry.required_params)

    lines: list[str] = []

    # 缺少必填参数 — 最重要的提示
    missing_required = required - used_keys
    if missing_required:
        parts = [f"{k}: {entry.param_docs.get(k, '')}" for k in missing_required]
        lines.append(f"⚠ 缺少必填参数: {'; '.join(parts)}")

    # 已传参数的 hint（歧义消解）
    for key in used_keys:
        hint = entry.param_hints.get(key)
        if hint:
            lines.append(f"💡 {key}: {hint}")

    # 未传但可能有用的参数（最多提示 3 个）
    useful = []
    for key in unused_keys:
        if key in required:
            continue  # 已在 missing_required 里提示过
        doc = entry.param_docs.get(key, "")
        if doc:
            useful.append(f"{key}: {doc}")
    if useful:
        lines.append(f"📎 其他可用参数: {'; '.join(useful[:3])}")

    return "\n".join(lines)
