"""Non-destructive preflight of the actual Skill model request, including tools."""
import json

from core.exceptions import AppException


class SkillContextBudgetError(AppException):
    def __init__(self):
        super().__init__('SKILL_CONTEXT_WINDOW_EXCEEDED',
            '当前 Skill、附件和历史的估算长度超过模型上下文容量。请减少资料或选择更大上下文的模型。', 422)


def assess_context(messages, tools, context_window, output_tokens=None):
    from services.handlers.context_compressor.tokens import estimate_tokens
    from services.agent.observability.chat_lifecycle import chat_phase
    tool_text = json.dumps(tools or [], ensure_ascii=False, separators=(',', ':'))
    estimated = estimate_tokens(messages) + estimate_tokens([{'content': tool_text}]) + 8 * len(messages)
    window = context_window if type(context_window) is int and context_window > 0 else None
    reserve = (output_tokens if type(output_tokens) is int and output_tokens > 0 else
               min(4096, max(1, window // 8)) if window else 4096)
    metrics = {'estimated_input_tokens': estimated, 'output_reserve_tokens': reserve,
               'context_window': window or 0, 'tool_count': len(tools or [])}
    chat_phase('skill.context_budget', metrics=metrics)
    if window is not None and estimated + reserve > window:
        raise SkillContextBudgetError()
    return metrics
