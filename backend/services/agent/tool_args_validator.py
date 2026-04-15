"""工具参数校验网关

架构层统一拦截 LLM 幻觉参数 + 必填缺失，位于 JSON 解析之后、工具执行之前。
适用于所有工具（local / remote / code），不依赖具体工具实现。

设计原则：
- Schema 来源 = selected_tools（传给模型的工具定义本身），单一数据源
- 幻觉参数静默丢弃 + 日志记录（不中断执行）
- 必填缺失 → 返回错误信息让模型重试
- 纯函数，无状态，可独立单测
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


def _lookup_schema(
    tool_name: str,
    selected_tools: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """从 selected_tools 中查找工具的 parameters schema。

    selected_tools 格式（OpenAI function calling）:
    [{"type": "function", "function": {"name": "xxx", "parameters": {...}}}]
    """
    for tool_def in selected_tools:
        func_def = tool_def.get("function", {})
        if func_def.get("name") == tool_name:
            return func_def.get("parameters")
    return None


def validate_tool_args(
    tool_name: str,
    args: Dict[str, Any],
    selected_tools: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Optional[str]]:
    """校验并清洗工具参数。

    Returns:
        (cleaned_args, error_msg)
        - error_msg=None  → 校验通过，使用 cleaned_args 执行
        - error_msg=str   → 校验失败，将 error_msg 回传模型重试
    """
    schema = _lookup_schema(tool_name, selected_tools)
    if schema is None:
        # 工具不在 selected_tools 中（动态注入等场景），跳过校验
        return args, None

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    # ── 1. 过滤幻觉参数：只保留 schema 中声明的 key ──
    valid_keys = set(properties.keys())
    hallucinated = set(args.keys()) - valid_keys
    if hallucinated:
        logger.warning(
            f"ToolArgsValidator hallucinated params stripped | "
            f"tool={tool_name} | dropped={hallucinated}"
        )
    cleaned = {k: v for k, v in args.items() if k in valid_keys}

    # ── 2. 必填参数缺失检查 ──
    missing = required - set(cleaned.keys())
    if missing:
        # 构建可用参数提示，帮助模型自行纠正
        param_hints = []
        for key in sorted(missing):
            prop = properties.get(key, {})
            desc = prop.get("description", "")
            enum_vals = prop.get("enum")
            hint = f"  - {key}"
            if desc:
                hint += f": {desc}"
            if enum_vals:
                hint += f" (可选值: {', '.join(str(v) for v in enum_vals)})"
            param_hints.append(hint)

        error_msg = (
            f"参数校验失败 — 缺少必填参数:\n"
            + "\n".join(param_hints)
            + "\n请调用 ask_user 向用户确认缺失的参数，禁止自行猜测参数值。"
        )
        return cleaned, error_msg

    return cleaned, None
