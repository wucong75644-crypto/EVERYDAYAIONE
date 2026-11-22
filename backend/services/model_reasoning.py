"""Explicit off is distinct from a provider's low/minimal reasoning setting."""
from core.exceptions import AppException


class ReasoningUnsupportedError(AppException):
    def __init__(self):
        super().__init__('MODEL_REASONING_OFF_UNSUPPORTED',
            '当前模型接口不支持关闭思考。请选择支持关闭的模型，或明确开启思考后重试。', 422)


def validate_reasoning(model_id, provider, mode):
    if mode != 'disabled':
        return
    name = getattr(provider, 'value', provider)
    # These interfaces expose effort levels, but no verified off setting.
    if (name in {'kie', 'google'} or model_id in {'kimi-k3', 'deepseek-r1', 'google/gemini-3.1-pro-preview'}
            or model_id in {'openai/o3', 'openai/o3-pro', 'openai/o4-mini', 'openai/gpt-5.4-pro'}):
        raise ReasoningUnsupportedError()


def dashscope_effort(model_id, mode, effort):
    validate_reasoning(model_id, 'dashscope', mode)
    if mode == 'disabled':
        return 'none'
    return effort or ('medium' if mode in ('enabled', 'deep_think') else 'none')


def openrouter_reasoning(mode, effort):
    if mode == 'disabled':
        return {'enabled': False}
    if effort is not None:
        return {'effort': effort}
    if mode in ('enabled', 'deep_think'):
        return {'enabled': True}
    return None


def supports_off(model_id, provider):
    try:
        validate_reasoning(model_id, provider, 'disabled')
    except ReasoningUnsupportedError:
        return False
    return True
