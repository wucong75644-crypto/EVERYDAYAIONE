"""
DashScope (阿里云百炼) 适配器

通过 OpenAI 兼容接口调用百炼平台模型：
DeepSeek、Qwen、Kimi、GLM 等。
"""

from .chat_adapter import DashScopeChatAdapter

__all__ = ["DashScopeChatAdapter"]
