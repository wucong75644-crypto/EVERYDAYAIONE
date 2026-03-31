"""
模型工厂 + 注册表

参考：
- One API: 集中管理模型映射（渠道配置）
- LiteLLM: model_list 配置驱动
"""

from typing import Dict, Optional

from loguru import logger

from core.config import get_settings
from .base import (
    BaseChatAdapter,
    BaseImageAdapter,
    BaseVideoAdapter,
    ModelProvider,
    ModelConfig,
)
from .types import ProviderUnavailableError


# ============================================================
# 模型注册表（集中管理，易于扩展）
# ============================================================

MODEL_REGISTRY: Dict[str, ModelConfig] = {
    # ==================== KIE 平台模型 ====================
    "gemini-3-pro": ModelConfig(
        model_id="gemini-3-pro",
        provider=ModelProvider.KIE,
        provider_model="gemini-3-pro",
        display_name="Gemini 3 Pro",
        input_price=0.50,       # $0.50 / 1M
        output_price=3.50,      # $3.50 / 1M
        credits_per_1k_input=1,
        credits_per_1k_output=7,
        supports_vision=True,
        supports_video=True,
        supports_tools=True,
        supports_search=True,
        supports_thinking=True,
        supports_structured_output=True,
        max_tokens=65536,
        context_window=1_000_000,
    ),
    "gemini-3-flash": ModelConfig(
        model_id="gemini-3-flash",
        provider=ModelProvider.KIE,
        provider_model="gemini-3-flash",
        display_name="Gemini 3 Flash",
        input_price=0.15,       # $0.15 / 1M
        output_price=0.90,      # $0.90 / 1M
        credits_per_1k_input=0.3,
        credits_per_1k_output=1.8,
        supports_vision=True,
        supports_video=True,
        supports_tools=True,
        supports_search=True,
        supports_thinking=True,
        max_tokens=65536,
        context_window=1_000_000,
    ),

    # ==================== DashScope 百炼模型 ====================
    "deepseek-v3.2": ModelConfig(
        model_id="deepseek-v3.2",
        provider=ModelProvider.DASHSCOPE,
        provider_model="deepseek-v3.2",
        display_name="DeepSeek V3.2",
        input_price=0.28,       # ~2元/1M ≈ $0.28
        output_price=1.11,      # ~8元/1M ≈ $1.11
        credits_per_1k_input=0.029,
        credits_per_1k_output=0.113,
        supports_vision=False,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=65536,
        context_window=131_072,
    ),
    "deepseek-r1": ModelConfig(
        model_id="deepseek-r1",
        provider=ModelProvider.DASHSCOPE,
        provider_model="deepseek-r1",
        display_name="DeepSeek R1",
        input_price=0.56,       # ~4元/1M ≈ $0.56
        output_price=2.22,      # ~16元/1M ≈ $2.22
        credits_per_1k_input=0.057,
        credits_per_1k_output=0.225,
        supports_vision=False,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=16384,
        context_window=131_072,
    ),
    "qwen3.5-plus": ModelConfig(
        model_id="qwen3.5-plus",
        provider=ModelProvider.DASHSCOPE,
        provider_model="qwen3.5-plus",
        display_name="Qwen 3.5 Plus",
        input_price=0.11,       # 0.8元/1M ≈ $0.11
        output_price=0.67,      # 4.8元/1M ≈ $0.67
        credits_per_1k_input=0.012,
        credits_per_1k_output=0.068,
        supports_vision=True,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=65536,
        context_window=1_000_000,
    ),
    "kimi-k2.5": ModelConfig(
        model_id="kimi-k2.5",
        provider=ModelProvider.DASHSCOPE,
        provider_model="kimi-k2.5",
        display_name="Kimi K2.5",
        input_price=0.56,       # 4元/1M ≈ $0.56
        output_price=2.92,      # 21元/1M ≈ $2.92
        credits_per_1k_input=0.057,
        credits_per_1k_output=0.295,
        supports_vision=True,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=32768,
        context_window=262_144,
    ),
    "glm-5": ModelConfig(
        model_id="glm-5",
        provider=ModelProvider.DASHSCOPE,
        provider_model="glm-5",
        display_name="GLM 5",
        input_price=0.56,       # ~4元/1M ≈ $0.56
        output_price=2.50,      # ~18元/1M ≈ $2.50
        credits_per_1k_input=0.057,
        credits_per_1k_output=0.253,
        supports_vision=False,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=16384,
        context_window=202_752,
    ),

    # ==================== OpenRouter 模型（多家统一网关）====================
    "openai/gpt-4.1": ModelConfig(
        model_id="openai/gpt-4.1",
        provider=ModelProvider.OPENROUTER,
        provider_model="openai/gpt-4.1",
        display_name="GPT-4.1",
        input_price=2.00,       # $2.00 / 1M
        output_price=8.00,      # $8.00 / 1M
        credits_per_1k_input=0.4,
        credits_per_1k_output=1.6,
        supports_vision=True,
        supports_tools=True,
        supports_search=True,
        supports_structured_output=True,
        max_tokens=32768,
        context_window=1_047_576,
    ),
    "openai/gpt-4.1-mini": ModelConfig(
        model_id="openai/gpt-4.1-mini",
        provider=ModelProvider.OPENROUTER,
        provider_model="openai/gpt-4.1-mini",
        display_name="GPT-4.1 Mini",
        input_price=0.40,       # $0.40 / 1M
        output_price=1.60,      # $1.60 / 1M
        credits_per_1k_input=0.08,
        credits_per_1k_output=0.32,
        supports_vision=True,
        supports_tools=True,
        supports_search=True,
        supports_structured_output=True,
        max_tokens=32768,
        context_window=1_047_576,
    ),
    "openai/o4-mini": ModelConfig(
        model_id="openai/o4-mini",
        provider=ModelProvider.OPENROUTER,
        provider_model="openai/o4-mini",
        display_name="o4-mini",
        input_price=1.10,       # $1.10 / 1M
        output_price=4.40,      # $4.40 / 1M
        credits_per_1k_input=0.22,
        credits_per_1k_output=0.88,
        supports_vision=True,
        supports_tools=True,
        supports_search=True,
        supports_thinking=True,
        supports_structured_output=True,
        max_tokens=100000,
        context_window=200_000,
    ),
    "anthropic/claude-sonnet-4": ModelConfig(
        model_id="anthropic/claude-sonnet-4",
        provider=ModelProvider.OPENROUTER,
        provider_model="anthropic/claude-sonnet-4",
        display_name="Claude Sonnet 4",
        input_price=3.00,       # $3.00 / 1M
        output_price=15.00,     # $15.00 / 1M
        credits_per_1k_input=0.6,
        credits_per_1k_output=3.0,
        supports_vision=True,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=16384,
        context_window=200_000,
    ),
    "x-ai/grok-4.1-fast": ModelConfig(
        model_id="x-ai/grok-4.1-fast",
        provider=ModelProvider.OPENROUTER,
        provider_model="x-ai/grok-4.1-fast",
        display_name="Grok 4.1 Fast",
        input_price=0.20,       # $0.20 / 1M
        output_price=0.50,      # $0.50 / 1M
        credits_per_1k_input=0.04,
        credits_per_1k_output=0.1,
        supports_vision=False,
        supports_tools=True,
        supports_search=True,
        max_tokens=32768,
        context_window=2_000_000,
    ),
    "openai/gpt-5.4": ModelConfig(
        model_id="openai/gpt-5.4",
        provider=ModelProvider.OPENROUTER,
        provider_model="openai/gpt-5.4",
        display_name="GPT-5.4",
        input_price=2.50,       # $2.50 / 1M
        output_price=15.00,     # $15.00 / 1M
        credits_per_1k_input=0.5,
        credits_per_1k_output=3.0,
        supports_vision=True,
        supports_audio=True,
        supports_tools=True,
        supports_search=True,
        supports_structured_output=True,
        max_tokens=128000,
        context_window=1_050_000,
    ),
    "openai/gpt-5.4-pro": ModelConfig(
        model_id="openai/gpt-5.4-pro",
        provider=ModelProvider.OPENROUTER,
        provider_model="openai/gpt-5.4-pro",
        display_name="GPT-5.4 Pro",
        input_price=30.00,      # $30.00 / 1M
        output_price=180.00,    # $180.00 / 1M
        credits_per_1k_input=6.0,
        credits_per_1k_output=36.0,
        supports_vision=True,
        supports_audio=True,
        supports_tools=True,
        supports_search=True,
        supports_thinking=True,
        supports_structured_output=True,
        max_tokens=128000,
        context_window=1_050_000,
    ),
    "openai/gpt-5.3-codex": ModelConfig(
        model_id="openai/gpt-5.3-codex",
        provider=ModelProvider.OPENROUTER,
        provider_model="openai/gpt-5.3-codex",
        display_name="GPT-5.3 Codex",
        input_price=1.75,       # $1.75 / 1M
        output_price=14.00,     # $14.00 / 1M
        credits_per_1k_input=0.35,
        credits_per_1k_output=2.8,
        supports_vision=True,
        supports_tools=True,
        supports_search=True,
        supports_structured_output=True,
        max_tokens=65536,
        context_window=400_000,
    ),
    "google/gemini-3.1-pro-preview": ModelConfig(
        model_id="google/gemini-3.1-pro-preview",
        provider=ModelProvider.OPENROUTER,
        provider_model="google/gemini-3.1-pro-preview",
        display_name="Gemini 3.1 Pro",
        input_price=2.00,       # $2.00 / 1M
        output_price=12.00,     # $12.00 / 1M
        credits_per_1k_input=0.4,
        credits_per_1k_output=2.4,
        supports_vision=True,
        supports_video=True,
        supports_audio=True,
        supports_tools=True,
        supports_search=True,
        supports_thinking=True,
        supports_structured_output=True,
        max_tokens=65536,
        context_window=1_048_576,
    ),
    "anthropic/claude-sonnet-4.6": ModelConfig(
        model_id="anthropic/claude-sonnet-4.6",
        provider=ModelProvider.OPENROUTER,
        provider_model="anthropic/claude-sonnet-4.6",
        display_name="Claude Sonnet 4.6",
        input_price=3.00,       # $3.00 / 1M
        output_price=15.00,     # $15.00 / 1M
        credits_per_1k_input=0.6,
        credits_per_1k_output=3.0,
        supports_vision=True,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=16384,
        context_window=1_000_000,
    ),
    "anthropic/claude-opus-4.6": ModelConfig(
        model_id="anthropic/claude-opus-4.6",
        provider=ModelProvider.OPENROUTER,
        provider_model="anthropic/claude-opus-4.6",
        display_name="Claude Opus 4.6",
        input_price=5.00,       # $5.00 / 1M
        output_price=25.00,     # $25.00 / 1M
        credits_per_1k_input=1.0,
        credits_per_1k_output=5.0,
        supports_vision=True,
        supports_tools=True,
        supports_thinking=True,
        max_tokens=32768,
        context_window=1_000_000,
    ),

    # ==================== Google 官方模型（Phase 6）====================
    "gemini-2.5-flash": ModelConfig(
        model_id="gemini-2.5-flash",
        provider=ModelProvider.GOOGLE,
        provider_model="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        input_price=0,  # 免费层
        output_price=0,
        credits_per_1k_input=0,
        credits_per_1k_output=0,
        supports_vision=True,
        supports_video=True,
        supports_audio=True,
        supports_tools=True,
        max_tokens=8192,
        context_window=1_000_000,
    ),
    "gemini-2.5-pro": ModelConfig(
        model_id="gemini-2.5-pro",
        provider=ModelProvider.GOOGLE,
        provider_model="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        input_price=0,  # 免费层
        output_price=0,
        credits_per_1k_input=0,
        credits_per_1k_output=0,
        supports_vision=True,
        supports_video=True,
        supports_audio=True,
        supports_tools=True,
        max_tokens=8192,
        context_window=2_000_000,
    ),
}

# 默认模型（数据源：smart_models.json → smart_model_config.py）
from config.smart_model_config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
)

DEFAULT_MODEL_ID = DEFAULT_CHAT_MODEL


# ============================================================
# 图片模型注册表
# ============================================================

IMAGE_MODEL_REGISTRY: Dict[str, Dict] = {
    # KIE 平台图片模型
    "google/nano-banana": {
        "provider": ModelProvider.KIE,
        "provider_model": "google/nano-banana",
        "display_name": "Nano Banana (基础)",
        "credits_per_image": 4,
    },
    "google/nano-banana-edit": {
        "provider": ModelProvider.KIE,
        "provider_model": "google/nano-banana-edit",
        "display_name": "Nano Banana Edit",
        "credits_per_image": 6,
    },
    "nano-banana-pro": {
        "provider": ModelProvider.KIE,
        "provider_model": "nano-banana-pro",
        "display_name": "Nano Banana Pro (4K)",
        "credits_per_image": {"1K": 18, "2K": 18, "4K": 24},
    },
}

DEFAULT_IMAGE_MODEL_ID = DEFAULT_IMAGE_MODEL


# ============================================================
# 视频模型注册表
# ============================================================

VIDEO_MODEL_REGISTRY: Dict[str, Dict] = {
    # KIE 平台视频模型
    "sora-2-text-to-video": {
        "provider": ModelProvider.KIE,
        "provider_model": "sora-2-text-to-video",
        "display_name": "Sora 2 Text-to-Video",
        "credits_per_second": 3,
    },
    "sora-2-image-to-video": {
        "provider": ModelProvider.KIE,
        "provider_model": "sora-2-image-to-video",
        "display_name": "Sora 2 Image-to-Video",
        "credits_per_second": 3,
    },
    "sora-2-pro-storyboard": {
        "provider": ModelProvider.KIE,
        "provider_model": "sora-2-pro-storyboard",
        "display_name": "Sora 2 Pro Storyboard",
        "credits_by_duration": {"10": 150, "15": 270, "25": 270},
    },
}

DEFAULT_VIDEO_MODEL_ID = DEFAULT_VIDEO_MODEL


# ============================================================
# 工厂函数
# ============================================================


def _resolve_ai_key(
    org_id: Optional[str],
    provider: str,
    settings_attr: str,
    db=None,
) -> Optional[str]:
    """获取 AI Key：企业自配 > 平台默认。

    Args:
        org_id: 企业 ID（None=散客，用平台默认）
        provider: 提供商标识（dashscope/openrouter/kie/google）
        settings_attr: settings 上的属性名（如 dashscope_api_key）
        db: 数据库实例（有 org_id 时必须传入）
    """
    settings = get_settings()
    if org_id and db:
        from services.org.config_resolver import OrgConfigResolver
        resolver = OrgConfigResolver(db)
        org_key = resolver.get(org_id, f"ai_{provider}_api_key")
        if org_key:
            return org_key
    return getattr(settings, settings_attr, None)


def create_chat_adapter(
    model_id: Optional[str] = None,
    stream_timeout: Optional[float] = None,
    org_id: Optional[str] = None,
    db=None,
) -> BaseChatAdapter:
    """
    根据模型 ID 创建对应的聊天适配器

    Args:
        model_id: 模型 ID，为空则使用默认模型
        stream_timeout: 流式超时（秒），为空则根据模型类型自动解析
        org_id: 企业 ID（BYOK 时用企业自己的 Key）
        db: 数据库实例（BYOK 时需要读 org_configs）

    Returns:
        对应 Provider 的聊天适配器实例

    Raises:
        ValueError: 模型不存在或 Provider 未实现
    """
    settings = get_settings()

    # 获取模型配置
    actual_model_id = model_id if model_id in MODEL_REGISTRY else DEFAULT_MODEL_ID
    config = MODEL_REGISTRY[actual_model_id]

    # 熔断器检查
    from services.circuit_breaker import is_provider_available
    if not is_provider_available(config.provider):
        raise ProviderUnavailableError(
            f"Provider {config.provider.value} 熔断中，模型 {actual_model_id} 暂不可用",
            provider=config.provider,
        )

    # 自动解析超时
    if stream_timeout is None:
        from services.timeout_resolver import resolve_stream_timeout
        stream_timeout = resolve_stream_timeout(actual_model_id)

    logger.info(
        f"Chat adapter created | model={actual_model_id} | "
        f"provider={config.provider.value} | "
        f"provider_model={config.provider_model} | timeout={stream_timeout}s"
        f"{f' | org_id={org_id}' if org_id else ''}"
    )

    # 根据 Provider 创建适配器（AI Key 支持 BYOK：企业自配 > 平台默认）
    if config.provider == ModelProvider.KIE:
        from .kie import KieClient, KieChatAdapter

        api_key = _resolve_ai_key(org_id, "kie", "kie_api_key", db)
        if not api_key:
            raise ValueError("KIE API Key 未配置")

        client = KieClient(api_key, stream_timeout=stream_timeout)
        return KieChatAdapter(client, config.provider_model)

    elif config.provider == ModelProvider.DASHSCOPE:
        from .dashscope import DashScopeChatAdapter

        api_key = _resolve_ai_key(org_id, "dashscope", "dashscope_api_key", db)
        if not api_key:
            raise ValueError("DashScope API Key 未配置")

        return DashScopeChatAdapter(
            api_key=api_key,
            model=config.provider_model,
            base_url=settings.dashscope_base_url,
            stream_timeout=stream_timeout,
        )

    elif config.provider == ModelProvider.OPENROUTER:
        from .openrouter import OpenRouterChatAdapter

        api_key = _resolve_ai_key(org_id, "openrouter", "openrouter_api_key", db)
        if not api_key:
            raise ValueError("OpenRouter API Key 未配置")

        return OpenRouterChatAdapter(
            api_key=api_key,
            model=config.provider_model,
            base_url=settings.openrouter_base_url,
            app_title=settings.openrouter_app_title,
            stream_timeout=stream_timeout,
        )

    elif config.provider == ModelProvider.GOOGLE:
        from .google import GoogleChatAdapter

        api_key = _resolve_ai_key(org_id, "google", "google_api_key", db)
        if not api_key:
            raise ValueError("Google API Key 未配置")

        return GoogleChatAdapter(
            model_id=config.provider_model,
            api_key=api_key,
        )

    else:
        raise ValueError(f"Provider {config.provider} 暂未实现")


def get_model_config(model_id: str) -> Optional[ModelConfig]:
    """获取模型配置信息"""
    return MODEL_REGISTRY.get(model_id)


def get_all_models() -> Dict[str, ModelConfig]:
    """获取所有可用模型"""
    return MODEL_REGISTRY.copy()


def get_models_by_provider(provider: ModelProvider) -> Dict[str, ModelConfig]:
    """按 Provider 筛选模型"""
    return {
        k: v for k, v in MODEL_REGISTRY.items()
        if v.provider == provider
    }


# ============================================================
# 图片适配器工厂
# ============================================================


def create_image_adapter(model_id: Optional[str] = None) -> BaseImageAdapter:
    """
    根据模型 ID 创建对应的图片生成适配器

    Args:
        model_id: 模型 ID，为空则使用默认模型

    Returns:
        对应 Provider 的图片适配器实例

    Raises:
        ValueError: 模型不存在或 Provider 未实现

    示例:
        # 基础文生图
        adapter = create_image_adapter("google/nano-banana")

        # 高级文生图 (4K)
        adapter = create_image_adapter("nano-banana-pro")

        # 使用默认模型
        adapter = create_image_adapter()
    """
    settings = get_settings()

    # 获取模型配置
    actual_model_id = model_id if model_id in IMAGE_MODEL_REGISTRY else DEFAULT_IMAGE_MODEL_ID
    config = IMAGE_MODEL_REGISTRY[actual_model_id]

    # 熔断器检查
    from services.circuit_breaker import is_provider_available
    if not is_provider_available(config["provider"]):
        raise ProviderUnavailableError(
            f"Provider {config['provider'].value} 熔断中，模型 {actual_model_id} 暂不可用",
            provider=config["provider"],
        )

    logger.debug(f"Creating image adapter: model_id={actual_model_id}, provider={config['provider']}")

    # 根据 Provider 创建适配器
    if config["provider"] == ModelProvider.KIE:
        from .kie import KieClient, KieImageAdapter

        if not settings.kie_api_key:
            raise ValueError("KIE API Key 未配置")

        client = KieClient(settings.kie_api_key)
        return KieImageAdapter(client, config["provider_model"])

    else:
        raise ValueError(f"图片 Provider {config['provider']} 暂未实现")


def get_image_model_config(model_id: str) -> Optional[Dict]:
    """获取图片模型配置信息"""
    return IMAGE_MODEL_REGISTRY.get(model_id)


def get_all_image_models() -> Dict[str, Dict]:
    """获取所有可用图片模型"""
    return IMAGE_MODEL_REGISTRY.copy()


# ============================================================
# 视频适配器工厂
# ============================================================


def create_video_adapter(model_id: Optional[str] = None) -> BaseVideoAdapter:
    """
    根据模型 ID 创建对应的视频生成适配器

    Args:
        model_id: 模型 ID，为空则使用默认模型

    Returns:
        对应 Provider 的视频适配器实例

    Raises:
        ValueError: 模型不存在或 Provider 未实现

    示例:
        # 文生视频
        adapter = create_video_adapter("sora-2-text-to-video")

        # 图生视频
        adapter = create_video_adapter("sora-2-image-to-video")

        # 使用默认模型
        adapter = create_video_adapter()
    """
    settings = get_settings()

    # 获取模型配置
    actual_model_id = model_id if model_id in VIDEO_MODEL_REGISTRY else DEFAULT_VIDEO_MODEL_ID
    config = VIDEO_MODEL_REGISTRY[actual_model_id]

    # 熔断器检查
    from services.circuit_breaker import is_provider_available
    if not is_provider_available(config["provider"]):
        raise ProviderUnavailableError(
            f"Provider {config['provider'].value} 熔断中，模型 {actual_model_id} 暂不可用",
            provider=config["provider"],
        )

    logger.debug(f"Creating video adapter: model_id={actual_model_id}, provider={config['provider']}")

    # 根据 Provider 创建适配器
    if config["provider"] == ModelProvider.KIE:
        from .kie import KieClient, KieVideoAdapter

        if not settings.kie_api_key:
            raise ValueError("KIE API Key 未配置")

        client = KieClient(settings.kie_api_key)
        return KieVideoAdapter(client, config["provider_model"])

    else:
        raise ValueError(f"视频 Provider {config['provider']} 暂未实现")


def get_video_model_config(model_id: str) -> Optional[Dict]:
    """获取视频模型配置信息"""
    return VIDEO_MODEL_REGISTRY.get(model_id)


def get_all_video_models() -> Dict[str, Dict]:
    """获取所有可用视频模型"""
    return VIDEO_MODEL_REGISTRY.copy()
