"""Runtime-only model adapter factory with no application Settings dependency."""

from __future__ import annotations

from typing import Any

from services.adapters.base import ModelProvider
from services.adapters.factory import get_model_config


_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def create_runtime_chat_adapter(
    model_id: str, *, api_key: str, stream_timeout: float,
) -> Any:
    """Build one credential-bound adapter from non-secret static metadata."""
    if not api_key:
        raise ValueError("RUNTIME_PROVIDER_CREDENTIAL_REQUIRED")
    config = get_model_config(model_id)
    if config is None:
        raise ValueError(f"unknown model_id: {model_id}")
    if config.provider == ModelProvider.KIE:
        from services.adapters.kie import KieChatAdapter, KieClient
        client = KieClient(
            api_key, stream_timeout=stream_timeout,
            environment="agent_runtime",
        )
        return KieChatAdapter(client, config.provider_model)
    if config.provider == ModelProvider.DASHSCOPE:
        from services.adapters.dashscope import DashScopeChatAdapter
        return DashScopeChatAdapter(
            api_key=api_key, model=config.provider_model,
            base_url=_DASHSCOPE_BASE_URL, stream_timeout=stream_timeout,
        )
    if config.provider == ModelProvider.OPENROUTER:
        from services.adapters.openrouter import OpenRouterChatAdapter
        return OpenRouterChatAdapter(
            api_key=api_key, model=config.provider_model,
            base_url=_OPENROUTER_BASE_URL, app_title="EverydayAI",
            stream_timeout=stream_timeout,
        )
    if config.provider == ModelProvider.GOOGLE:
        from services.adapters.google import GoogleChatAdapter
        return GoogleChatAdapter(
            model_id=config.provider_model, api_key=api_key,
        )
    raise ValueError(f"RUNTIME_MODEL_PROVIDER_UNSUPPORTED:{config.provider.value}")
