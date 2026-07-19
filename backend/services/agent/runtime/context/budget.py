"""按模型能力推导全通道一致的上下文预算。"""

from __future__ import annotations

from dataclasses import dataclass


_DEFAULT_CONTEXT_WINDOW = 128_000
_DEFAULT_MAX_OUTPUT = 8_192


@dataclass(frozen=True)
class ContextBudget:
    """单个模型的输入容量与压缩阈值。"""

    context_window: int
    reserved_output: int
    safety_margin: int
    usable_input: int
    soft_compaction: int
    hard_compaction: int
    emergency_trim: int


def derive_context_budget(
    context_window: int,
    max_output_tokens: int,
) -> ContextBudget:
    """根据模型窗口和最大输出推导输入预算。"""
    window = max(1, int(context_window))
    max_output = max(0, int(max_output_tokens))
    reserved_output = max(max_output, int(window * 0.125))
    safety_margin = max(2_048, int(window * 0.05))
    usable_input = max(1, window - reserved_output - safety_margin)
    return ContextBudget(
        context_window=window,
        reserved_output=reserved_output,
        safety_margin=safety_margin,
        usable_input=usable_input,
        soft_compaction=max(1, int(usable_input * 0.75)),
        hard_compaction=max(1, int(usable_input * 0.85)),
        emergency_trim=max(1, int(usable_input * 0.92)),
    )


def resolve_context_budget(model_id: str | None) -> ContextBudget:
    """从统一模型注册表解析预算；未知模型使用保守默认能力。"""
    from services.adapters.factory import get_model_config

    config = get_model_config(model_id) if model_id else None
    if config is None:
        return derive_context_budget(
            _DEFAULT_CONTEXT_WINDOW,
            _DEFAULT_MAX_OUTPUT,
        )
    return derive_context_budget(config.context_window, config.max_tokens)
