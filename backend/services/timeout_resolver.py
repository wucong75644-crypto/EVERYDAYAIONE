"""
超时分级解析器

根据任务类型 + 模型能力动态选择合适的超时时间。
"""

from typing import Optional

from schemas.message import GenerationType


# 专用推理模型白名单（推理是核心能力，输出前有长 thinking 阶段）
_KNOWN_THINKING_MODELS = frozenset({
    "deepseek-r1",
    "openai/o4-mini",
    "openai/gpt-5.4-pro",
})


def is_thinking_model(model_id: str) -> bool:
    """判断模型是否为专用推理模型（需要更长超时）

    注意：MODEL_REGISTRY 中 supports_thinking 范围过广（几乎所有模型都支持），
    这里仅识别 *专用推理模型*，即推理是其核心能力、响应明显更慢的模型。
    """
    return model_id in _KNOWN_THINKING_MODELS


def resolve_stream_timeout(
    model_id: str,
    generation_type: Optional[GenerationType] = None,
) -> float:
    """解析流式/生成超时

    Args:
        model_id: 模型 ID
        generation_type: 生成类型（默认 CHAT）

    Returns:
        超时秒数
    """
    from core.config import get_settings

    settings = get_settings()

    if generation_type == GenerationType.IMAGE:
        return settings.image_generation_timeout

    if generation_type == GenerationType.VIDEO:
        return settings.video_generation_timeout

    # CHAT 类型：区分推理模型和普通模型
    if is_thinking_model(model_id):
        return settings.chat_thinking_timeout

    return settings.chat_stream_timeout
