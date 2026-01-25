"""
消息 AI 辅助函数

包含 AI 聊天和流式响应的辅助方法。
"""

from typing import Optional, List, Dict, Any, AsyncIterator

from loguru import logger
from supabase import Client

from core.config import get_settings
from services.adapters.kie.client import KieClient, KieAPIError
from services.adapters.kie.chat_adapter import KieChatAdapter
from services.adapters.kie.models import ReasoningEffort, ThinkingMode, MessageRole


def parse_thinking_effort(thinking_effort: Optional[str]) -> ReasoningEffort:
    """
    将前端 thinking_effort 字符串转换为 ReasoningEffort 枚举

    Args:
        thinking_effort: 前端传入的推理强度 ('minimal' | 'low' | 'medium' | 'high')

    Returns:
        ReasoningEffort 枚举值，默认为 LOW
    """
    if not thinking_effort:
        return ReasoningEffort.LOW  # 默认标准速度

    effort_map = {
        'minimal': ReasoningEffort.MINIMAL,
        'low': ReasoningEffort.LOW,
        'medium': ReasoningEffort.MEDIUM,
        'high': ReasoningEffort.HIGH,
    }

    return effort_map.get(thinking_effort.lower(), ReasoningEffort.LOW)


def parse_thinking_mode(thinking_mode: Optional[str]) -> Optional[ThinkingMode]:
    """
    将前端 thinking_mode 字符串转换为 ThinkingMode 枚举

    Args:
        thinking_mode: 前端传入的推理模式 ('default' | 'deep_think')

    Returns:
        ThinkingMode 枚举值，默认为 None（使用模型默认）
    """
    if not thinking_mode:
        return None

    mode_map = {
        'default': ThinkingMode.DEFAULT,
        'deep_think': ThinkingMode.DEEP_THINK,
    }

    return mode_map.get(thinking_mode.lower())


async def call_ai_chat(
    db: Client,
    get_conversation_history_func,
    conversation_id: str,
    user_id: str,
    user_message: str,
    model_id: Optional[str] = None,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
    thinking_effort: Optional[str] = None,
    thinking_mode: Optional[str] = None,
) -> tuple[str, int]:
    """
    调用 AI Chat 模型（非流式）

    Args:
        db: 数据库客户端
        get_conversation_history_func: 获取对话历史的函数
        conversation_id: 对话 ID
        user_id: 用户 ID
        user_message: 用户消息
        model_id: 模型 ID
        image_url: 图片 URL（可选，用于 VQA）
        video_url: 视频 URL（可选，用于视频 QA）
        thinking_effort: 推理强度（可选，Gemini 3 专用）
        thinking_mode: 推理模式（可选，Gemini 3 Pro Deep Think）

    Returns:
        (AI 回复内容, 消耗积分)
    """
    settings = get_settings()

    if not settings.kie_api_key:
        raise KieAPIError("KIE API key not configured")

    # 默认使用 gemini-3-flash
    model = model_id if model_id in ("gemini-3-pro", "gemini-3-flash") else "gemini-3-flash"

    # 获取对话历史作为上下文
    history = await get_conversation_history_func(conversation_id, user_id)

    # 创建适配器并调用
    client = KieClient(settings.kie_api_key)
    adapter = KieChatAdapter(client, model)

    # 解析推理强度和推理模式
    effort = parse_thinking_effort(thinking_effort)
    mode = parse_thinking_mode(thinking_mode)

    try:
        # 如果有图片或视频，使用 multimodal 消息
        if image_url or video_url:
            messages = adapter.format_messages_from_history(history)

            # 收集所有媒体URL
            media_urls = []
            if image_url:
                media_urls.append(image_url)
            if video_url:
                media_urls.append(video_url)

            messages.append(
                adapter.format_multimodal_message(
                    MessageRole.USER,
                    user_message,
                    media_urls
                )
            )
            response = await adapter.chat(
                messages=messages,
                stream=False,
                include_thoughts=False,
                reasoning_effort=effort,
                thinking_mode=mode,
            )
        else:
            response = await adapter.chat_simple(
                user_message=user_message,
                history=history,
                stream=False,
                include_thoughts=False,
                reasoning_effort=effort,
                thinking_mode=mode,
            )

        # 提取回复内容
        ai_content = ""
        if response.choices:
            delta = response.choices[0].delta
            ai_content = delta.content or ""

        # 计算积分消耗
        credits = 0
        if response.usage:
            cost = adapter.estimate_cost(
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
            credits = cost.estimated_credits

        logger.info(
            f"AI chat completed | conversation_id={conversation_id} | "
            f"model={model} | credits={credits}"
        )

        return ai_content, credits
    finally:
        await client.close()


def prepare_ai_stream_client(
    model_id: Optional[str]
) -> tuple[str, KieClient, KieChatAdapter]:
    """准备AI流式客户端"""
    settings = get_settings()
    model = model_id if model_id in ("gemini-3-pro", "gemini-3-flash") else "gemini-3-flash"
    client = KieClient(settings.kie_api_key)
    adapter = KieChatAdapter(client, model)
    return model, client, adapter


async def stream_ai_response(
    adapter: KieChatAdapter,
    get_conversation_history_func,
    conversation_id: str,
    user_id: str,
    content: str,
    image_url: Optional[str],
    video_url: Optional[str],
    thinking_effort: Optional[str] = None,
    thinking_mode: Optional[str] = None,
) -> AsyncIterator:
    """
    准备并返回AI流式响应

    Args:
        adapter: KIE 聊天适配器
        get_conversation_history_func: 获取对话历史的函数
        conversation_id: 对话 ID
        user_id: 用户 ID
        content: 用户消息内容
        image_url: 图片 URL
        video_url: 视频 URL
        thinking_effort: 推理强度（可选，Gemini 3 专用）
        thinking_mode: 推理模式（可选，Gemini 3 Pro Deep Think）

    Returns:
        异步迭代器
    """
    history = await get_conversation_history_func(conversation_id, user_id)

    # 解析推理强度和推理模式
    effort = parse_thinking_effort(thinking_effort)
    mode = parse_thinking_mode(thinking_mode)

    # 根据是否有媒体选择不同的消息格式
    if image_url or video_url:
        messages = adapter.format_messages_from_history(history)
        media_urls = []
        if image_url:
            media_urls.append(image_url)
        if video_url:
            media_urls.append(video_url)

        messages.append(
            adapter.format_multimodal_message(MessageRole.USER, content, media_urls)
        )
        stream = await adapter.chat(
            messages=messages,
            stream=True,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
        )
    else:
        stream = await adapter.chat_simple(
            user_message=content,
            history=history,
            stream=True,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
        )

    return stream
