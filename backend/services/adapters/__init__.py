"""
AI 模型适配器包

提供统一的模型访问接口，支持多 Provider：
- KIE AI 平台（Gemini 3 Pro/Flash）
- Google 官方 Gemini（Phase 6）
- OpenRouter（GPT-4.1、Claude Sonnet 4、Grok 等多家模型）
- OpenAI（预留）
- Anthropic（预留）

使用示例:
    from services.adapters import create_chat_adapter

    # 使用工厂创建适配器（推荐）
    adapter = create_chat_adapter("gemini-3-flash")

    # 流式聊天
    async for chunk in adapter.stream_chat(messages):
        print(chunk.content, end="")

    # 成本估算
    cost = adapter.estimate_cost_unified(1000, 500)
    print(f"Credits: {cost.estimated_credits}")

    # 关闭连接
    await adapter.close()
"""

# 基类和数据模型
from .base import (
    # 抽象基类
    BaseChatAdapter,
    BaseImageAdapter,
    BaseVideoAdapter,
    # 枚举
    ModelProvider,
    MediaType,
    TaskStatus,
    # 数据模型
    StreamChunk,
    ChatResponse,
    CostEstimate,
    ModelConfig,
    MultimodalPart,
    UnifiedMessage,
    ImageGenerateResult,
    VideoGenerateResult,
)

# 工厂函数
from .factory import (
    # Chat
    create_chat_adapter,
    get_model_config,
    get_all_models,
    get_models_by_provider,
    MODEL_REGISTRY,
    DEFAULT_MODEL_ID,
    # Image
    create_image_adapter,
    get_image_model_config,
    get_all_image_models,
    IMAGE_MODEL_REGISTRY,
    DEFAULT_IMAGE_MODEL_ID,
    # Video
    create_video_adapter,
    get_video_model_config,
    get_all_video_models,
    VIDEO_MODEL_REGISTRY,
    DEFAULT_VIDEO_MODEL_ID,
)

# KIE 适配器（保持向后兼容）
from .kie import (
    KieClient,
    KieChatAdapter,
    KieImageAdapter,
    KieVideoAdapter,
    KieAPIError,
)

# OpenRouter 适配器
from .openrouter import OpenRouterChatAdapter, OpenRouterAPIError


__all__ = [
    # 基类
    "BaseChatAdapter",
    "BaseImageAdapter",
    "BaseVideoAdapter",
    # 枚举
    "ModelProvider",
    "MediaType",
    "TaskStatus",
    # 数据模型
    "StreamChunk",
    "ChatResponse",
    "CostEstimate",
    "ModelConfig",
    "MultimodalPart",
    "UnifiedMessage",
    "ImageGenerateResult",
    "VideoGenerateResult",
    # Chat 工厂
    "create_chat_adapter",
    "get_model_config",
    "get_all_models",
    "get_models_by_provider",
    "MODEL_REGISTRY",
    "DEFAULT_MODEL_ID",
    # Image 工厂
    "create_image_adapter",
    "get_image_model_config",
    "get_all_image_models",
    "IMAGE_MODEL_REGISTRY",
    "DEFAULT_IMAGE_MODEL_ID",
    # Video 工厂
    "create_video_adapter",
    "get_video_model_config",
    "get_all_video_models",
    "VIDEO_MODEL_REGISTRY",
    "DEFAULT_VIDEO_MODEL_ID",
    # KIE 适配器（向后兼容）
    "KieClient",
    "KieChatAdapter",
    "KieImageAdapter",
    "KieVideoAdapter",
    "KieAPIError",
    # OpenRouter 适配器
    "OpenRouterChatAdapter",
    "OpenRouterAPIError",
]

__version__ = "2.0.0"  # 统一适配器版本
