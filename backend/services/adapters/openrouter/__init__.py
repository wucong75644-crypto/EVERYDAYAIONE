"""
OpenRouter 适配器包

通过 OpenRouter 统一网关调用多家 AI 模型（OpenAI、Anthropic、Google、xAI 等）。
"""

from .chat_adapter import OpenRouterChatAdapter, OpenRouterAPIError

__all__ = ["OpenRouterChatAdapter", "OpenRouterAPIError"]
