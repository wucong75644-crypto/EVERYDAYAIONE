"""
Google Gemini API 适配器

使用新的 google-genai SDK（GA 状态）实现与 Google 官方 Gemini API 的对接。

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

版本: 2.0（使用 google-genai SDK）
"""

from .client import GoogleClient
from .chat_adapter import GoogleChatAdapter

__all__ = ["GoogleClient", "GoogleChatAdapter"]
