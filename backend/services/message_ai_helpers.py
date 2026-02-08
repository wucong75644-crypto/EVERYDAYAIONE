"""
消息 AI 辅助函数

包含 AI 聊天和流式响应的辅助方法。

Phase 3 改造：使用统一工厂 create_chat_adapter() 创建适配器
"""

from typing import Optional, List, Dict, Any, AsyncIterator

from loguru import logger
from supabase import Client

from services.adapters import (
    create_chat_adapter,
    BaseChatAdapter,
    StreamChunk,
    DEFAULT_MODEL_ID,
)


def normalize_thinking_effort(thinking_effort: Optional[str]) -> Optional[str]:
    """
    规范化推理强度参数

    Args:
        thinking_effort: 前端传入的推理强度 ('minimal' | 'low' | 'medium' | 'high')

    Returns:
        规范化后的字符串，默认为 'low'
    """
    if not thinking_effort:
        return "low"  # 默认标准速度

    valid_values = {'minimal', 'low', 'medium', 'high'}
    normalized = thinking_effort.lower()
    return normalized if normalized in valid_values else "low"


def normalize_thinking_mode(thinking_mode: Optional[str]) -> Optional[str]:
    """
    规范化推理模式参数

    Args:
        thinking_mode: 前端传入的推理模式 ('default' | 'deep_think')

    Returns:
        规范化后的字符串，或 None
    """
    if not thinking_mode:
        return None

    valid_values = {'default', 'deep_think'}
    normalized = thinking_mode.lower()
    return normalized if normalized in valid_values else None


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

    使用统一工厂创建适配器，支持多 Provider。

    Args:
        db: 数据库客户端
        get_conversation_history_func: 获取对话历史的函数
        conversation_id: 对话 ID
        user_id: 用户 ID
        user_message: 用户消息
        model_id: 模型 ID
        image_url: 图片 URL（可选，用于 VQA）
        video_url: 视频 URL（可选，用于视频 QA）
        thinking_effort: 推理强度（可选）
        thinking_mode: 推理模式（可选）

    Returns:
        (AI 回复内容, 消耗积分)
    """
    # 使用工厂创建适配器（自动选择 Provider）
    adapter = create_chat_adapter(model_id)
    model = model_id or DEFAULT_MODEL_ID

    # 获取对话历史作为上下文
    history = await get_conversation_history_func(conversation_id, user_id)

    # 规范化参数
    effort = normalize_thinking_effort(thinking_effort)
    mode = normalize_thinking_mode(thinking_mode)

    try:
        # 构建统一格式消息
        messages = _build_messages_for_chat(history, user_message, image_url, video_url)

        # 使用统一接口调用
        response = await adapter.chat_sync(
            messages=messages,
            reasoning_effort=effort,
            thinking_mode=mode,
        )

        # 提取回复内容
        ai_content = response.content or ""

        # 计算积分消耗
        cost = adapter.estimate_cost_unified(
            response.prompt_tokens,
            response.completion_tokens,
        )
        credits = cost.estimated_credits

        logger.info(
            f"AI chat completed | conversation_id={conversation_id} | "
            f"model={model} | credits={credits}"
        )

        return ai_content, credits
    finally:
        await adapter.close()


def _build_messages_for_chat(
    history: List[Dict[str, Any]],
    user_message: str,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    构建统一格式的消息列表

    Args:
        history: 对话历史
        user_message: 当前用户消息
        image_url: 图片 URL（可选）
        video_url: 视频 URL（可选）

    Returns:
        OpenAI 兼容格式的消息列表
    """
    messages = []

    # 添加历史消息
    for msg in history:
        messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })

    # 添加当前用户消息
    if image_url or video_url:
        # 多模态消息
        content_parts = [{"type": "text", "text": user_message}]

        # 处理图片（支持逗号分隔的多图）
        if image_url:
            for url in image_url.split(','):
                url = url.strip()
                if url:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": url}
                    })

        # 处理视频
        if video_url:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": video_url}
            })

        messages.append({"role": "user", "content": content_parts})
    else:
        # 纯文本消息
        messages.append({"role": "user", "content": user_message})

    return messages


def prepare_chat_adapter(
    model_id: Optional[str]
) -> tuple[str, BaseChatAdapter]:
    """
    准备聊天适配器（使用统一工厂）

    Args:
        model_id: 模型 ID（可选，为空则使用默认模型）

    Returns:
        (模型名称, 适配器实例)
    """
    adapter = create_chat_adapter(model_id)
    model = model_id or DEFAULT_MODEL_ID
    return model, adapter


async def stream_ai_response(
    adapter: BaseChatAdapter,
    get_conversation_history_func,
    conversation_id: str,
    user_id: str,
    content: str,
    image_url: Optional[str],
    video_url: Optional[str],
    thinking_effort: Optional[str] = None,
    thinking_mode: Optional[str] = None,
) -> AsyncIterator[StreamChunk]:
    """
    准备并返回AI流式响应（使用统一接口）

    Args:
        adapter: 聊天适配器（BaseChatAdapter）
        get_conversation_history_func: 获取对话历史的函数
        conversation_id: 对话 ID
        user_id: 用户 ID
        content: 用户消息内容
        image_url: 图片 URL
        video_url: 视频 URL
        thinking_effort: 推理强度（可选）
        thinking_mode: 推理模式（可选）

    Returns:
        统一格式的流式响应迭代器 (StreamChunk)
    """
    history = await get_conversation_history_func(conversation_id, user_id)

    # 规范化参数
    effort = normalize_thinking_effort(thinking_effort)
    mode = normalize_thinking_mode(thinking_mode)

    # 构建统一格式消息
    messages = _build_messages_for_chat(history, content, image_url, video_url)

    # 使用统一接口获取流式响应
    async for chunk in adapter.stream_chat(
        messages=messages,
        reasoning_effort=effort,
        thinking_mode=mode,
    ):
        yield chunk
