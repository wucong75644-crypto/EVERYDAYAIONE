"""
Google Gemini API 适配器

使用新的 google-genai SDK（GA 状态）实现与 Google 官方 Gemini API 的对接。

支持模型:
- gemini-2.5-flash: 高效能模型
- gemini-2.5-pro: 高级推理模型

生产 Chat 调用通过 services.model_gateway.get_model_gateway() 进入共享 Gateway；
本模块只暴露 Provider adapter 实现。

版本: 2.0（使用 google-genai SDK）
"""

from .client import GoogleClient
from .chat_adapter import GoogleChatAdapter

__all__ = ["GoogleClient", "GoogleChatAdapter"]
