"""将现有工具安全元数据投影为 Validation ToolEffect。"""

from __future__ import annotations

from services.agent.runtime.validation.types import ToolEffect


def resolve_tool_effect(tool_name: str) -> ToolEffect:
    """危险工具按非幂等写处理；未知元数据安全默认只读。"""
    try:
        from config.chat_tools import SafetyLevel, get_safety_level

        if get_safety_level(tool_name) == SafetyLevel.DANGEROUS:
            return ToolEffect.NON_IDEMPOTENT_WRITE
    except Exception:
        return ToolEffect.READ_ONLY
    return ToolEffect.READ_ONLY
