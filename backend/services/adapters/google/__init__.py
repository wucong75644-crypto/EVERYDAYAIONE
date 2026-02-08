"""
Google 官方 Gemini API 适配器

支持模型:
- gemini-2.5-flash: 高效能模型
- gemini-2.5-pro: 高级推理模型

使用示例:
    from services.adapters.google import GoogleChatAdapter

    adapter = GoogleChatAdapter(
        model_id="gemini-2.5-flash-preview-05-20",
        api_key="your-api-key"
    )

    async for chunk in adapter.stream_chat(messages):
        print(chunk.content, end="")

    await adapter.close()
"""

from .chat_adapter import GoogleChatAdapter

__all__ = ["GoogleChatAdapter"]
