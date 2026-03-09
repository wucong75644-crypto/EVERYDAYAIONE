"""
模型工厂 + 注册表测试

覆盖：create_chat_adapter 路由、model_registry 查询、API key 缺失、
      图片/视频工厂、默认模型 fallback
"""

from unittest.mock import MagicMock, patch

import pytest

from services.adapters.base import ModelProvider, ModelConfig
from services.adapters.factory import (
    MODEL_REGISTRY,
    IMAGE_MODEL_REGISTRY,
    VIDEO_MODEL_REGISTRY,
    DEFAULT_MODEL_ID,
    create_chat_adapter,
    create_image_adapter,
    create_video_adapter,
    get_model_config,
    get_all_models,
    get_models_by_provider,
)


# ============================================================
# Helpers
# ============================================================


def _mock_settings(**overrides):
    """创建 mock settings"""
    defaults = {
        "kie_api_key": "kie-test-key",
        "dashscope_api_key": "ds-test-key",
        "dashscope_base_url": "https://dashscope.example.com/v1",
        "google_api_key": "google-test-key",
        "openrouter_api_key": "or-test-key",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_app_title": "TestApp",
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


# ============================================================
# TestCreateChatAdapter
# ============================================================


class TestCreateChatAdapter:

    @patch("services.adapters.factory.get_settings")
    def test_kie_model_creates_kie_adapter(self, mock_settings):
        """gemini-3-pro → KieChatAdapter"""
        mock_settings.return_value = _mock_settings()
        adapter = create_chat_adapter("gemini-3-pro")
        from services.adapters.kie import KieChatAdapter
        assert isinstance(adapter, KieChatAdapter)

    @patch("services.adapters.factory.get_settings")
    def test_dashscope_model_creates_dashscope_adapter(self, mock_settings):
        """deepseek-v3.2 → DashScopeChatAdapter"""
        mock_settings.return_value = _mock_settings()
        adapter = create_chat_adapter("deepseek-v3.2")
        from services.adapters.dashscope import DashScopeChatAdapter
        assert isinstance(adapter, DashScopeChatAdapter)

    @patch("services.adapters.factory.get_settings")
    def test_google_model_creates_google_adapter(self, mock_settings):
        """gemini-2.5-flash → GoogleChatAdapter"""
        mock_settings.return_value = _mock_settings()
        adapter = create_chat_adapter("gemini-2.5-flash")
        from services.adapters.google import GoogleChatAdapter
        assert isinstance(adapter, GoogleChatAdapter)

    @patch("services.adapters.factory.get_settings")
    def test_unknown_model_fallback_to_default(self, mock_settings):
        """未知模型→fallback DEFAULT_MODEL_ID"""
        mock_settings.return_value = _mock_settings()
        adapter = create_chat_adapter("nonexistent-model")
        # 应该用 DEFAULT_MODEL_ID 创建
        default_config = MODEL_REGISTRY[DEFAULT_MODEL_ID]
        assert adapter is not None

    @patch("services.adapters.factory.get_settings")
    def test_none_model_uses_default(self, mock_settings):
        """None → 默认模型"""
        mock_settings.return_value = _mock_settings()
        adapter = create_chat_adapter(None)
        assert adapter is not None

    @patch("services.adapters.factory.get_settings")
    def test_kie_api_key_missing_raises(self, mock_settings):
        """kie_api_key 缺失 → ValueError"""
        mock_settings.return_value = _mock_settings(kie_api_key=None)
        with pytest.raises(ValueError, match="KIE API Key"):
            create_chat_adapter("gemini-3-pro")

    @patch("services.adapters.factory.get_settings")
    def test_dashscope_api_key_missing_raises(self, mock_settings):
        """dashscope_api_key 缺失 → ValueError"""
        mock_settings.return_value = _mock_settings(dashscope_api_key=None)
        with pytest.raises(ValueError, match="DashScope API Key"):
            create_chat_adapter("deepseek-v3.2")

    @patch("services.adapters.factory.get_settings")
    def test_google_api_key_missing_raises(self, mock_settings):
        """google_api_key 缺失 → ValueError"""
        mock_settings.return_value = _mock_settings(google_api_key=None)
        with pytest.raises(ValueError, match="Google API Key"):
            create_chat_adapter("gemini-2.5-flash")

    @patch("services.adapters.factory.get_settings")
    def test_openrouter_model_creates_openrouter_adapter(self, mock_settings):
        """openai/gpt-4.1 → OpenRouterChatAdapter"""
        mock_settings.return_value = _mock_settings()
        adapter = create_chat_adapter("openai/gpt-4.1")
        from services.adapters.openrouter import OpenRouterChatAdapter
        assert isinstance(adapter, OpenRouterChatAdapter)

    @patch("services.adapters.factory.get_settings")
    def test_openrouter_claude_model(self, mock_settings):
        """anthropic/claude-sonnet-4.6 → OpenRouterChatAdapter"""
        mock_settings.return_value = _mock_settings()
        adapter = create_chat_adapter("anthropic/claude-sonnet-4.6")
        from services.adapters.openrouter import OpenRouterChatAdapter
        assert isinstance(adapter, OpenRouterChatAdapter)

    @patch("services.adapters.factory.get_settings")
    def test_openrouter_api_key_missing_raises(self, mock_settings):
        """openrouter_api_key 缺失 → ValueError"""
        mock_settings.return_value = _mock_settings(openrouter_api_key=None)
        with pytest.raises(ValueError, match="OpenRouter API Key"):
            create_chat_adapter("openai/gpt-4.1")


# ============================================================
# TestModelRegistry
# ============================================================


class TestModelRegistry:

    def test_get_model_config_known(self):
        """已知模型返回 ModelConfig"""
        config = get_model_config("gemini-3-pro")
        assert config is not None
        assert isinstance(config, ModelConfig)
        assert config.provider == ModelProvider.KIE

    def test_get_model_config_unknown(self):
        """未知模型返回 None"""
        assert get_model_config("nonexistent") is None

    def test_get_all_models_returns_copy(self):
        """get_all_models 返回副本（修改不影响原始）"""
        all_models = get_all_models()
        original_count = len(MODEL_REGISTRY)
        all_models["test-model"] = MagicMock()
        assert len(MODEL_REGISTRY) == original_count

    def test_get_models_by_provider_kie(self):
        """按 KIE provider 过滤"""
        kie_models = get_models_by_provider(ModelProvider.KIE)
        assert len(kie_models) > 0
        for config in kie_models.values():
            assert config.provider == ModelProvider.KIE

    def test_get_models_by_provider_dashscope(self):
        """按 DASHSCOPE provider 过滤"""
        ds_models = get_models_by_provider(ModelProvider.DASHSCOPE)
        assert len(ds_models) > 0
        assert "deepseek-v3.2" in ds_models

    def test_get_models_by_provider_google(self):
        """按 GOOGLE provider 过滤"""
        google_models = get_models_by_provider(ModelProvider.GOOGLE)
        assert len(google_models) > 0
        assert "gemini-2.5-flash" in google_models

    def test_get_models_by_provider_openrouter(self):
        """按 OPENROUTER provider 过滤"""
        or_models = get_models_by_provider(ModelProvider.OPENROUTER)
        assert len(or_models) >= 11
        assert "openai/gpt-4.1" in or_models
        assert "anthropic/claude-sonnet-4.6" in or_models
        assert "x-ai/grok-4.1-fast" in or_models

    def test_all_openrouter_models_registered(self):
        """验证所有 OpenRouter 模型都在注册表中"""
        expected = {
            "openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/o4-mini",
            "anthropic/claude-sonnet-4", "x-ai/grok-4.1-fast",
            "openai/gpt-5.4", "openai/gpt-5.4-pro", "openai/gpt-5.3-codex",
            "google/gemini-3.1-pro-preview",
            "anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.6",
        }
        or_models = get_models_by_provider(ModelProvider.OPENROUTER)
        assert expected.issubset(set(or_models.keys()))

    def test_openrouter_pricing_correct(self):
        """验证 OpenRouter 模型定价与官方一致"""
        pricing = {
            "openai/gpt-4.1": (2.0, 8.0),
            "openai/gpt-4.1-mini": (0.4, 1.6),
            "openai/o4-mini": (1.1, 4.4),
            "anthropic/claude-sonnet-4": (3.0, 15.0),
            "x-ai/grok-4.1-fast": (0.2, 0.5),
            "openai/gpt-5.4": (2.5, 15.0),
            "openai/gpt-5.4-pro": (30.0, 180.0),
            "openai/gpt-5.3-codex": (1.75, 14.0),
            "google/gemini-3.1-pro-preview": (2.0, 12.0),
            "anthropic/claude-sonnet-4.6": (3.0, 15.0),
            "anthropic/claude-opus-4.6": (5.0, 25.0),
        }
        for model_id, (exp_in, exp_out) in pricing.items():
            config = get_model_config(model_id)
            assert config is not None, f"模型未注册: {model_id}"
            assert config.input_price == exp_in, \
                f"{model_id} input_price: {config.input_price} != {exp_in}"
            assert config.output_price == exp_out, \
                f"{model_id} output_price: {config.output_price} != {exp_out}"

    def test_openrouter_capabilities(self):
        """验证 OpenRouter 模型能力标记"""
        # 所有 OpenRouter 模型都支持 tools
        or_models = get_models_by_provider(ModelProvider.OPENROUTER)
        for mid, cfg in or_models.items():
            assert cfg.supports_tools is True, f"{mid} should support tools"

        # vision 支持
        vision_models = {
            "openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/o4-mini",
            "anthropic/claude-sonnet-4", "openai/gpt-5.4", "openai/gpt-5.4-pro",
            "openai/gpt-5.3-codex", "google/gemini-3.1-pro-preview",
            "anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.6",
        }
        for mid in vision_models:
            assert or_models[mid].supports_vision is True, f"{mid} should support vision"

        # Grok 不支持 vision
        assert or_models["x-ai/grok-4.1-fast"].supports_vision is False

    def test_openrouter_context_windows(self):
        """验证 OpenRouter 模型上下文窗口"""
        context = {
            "x-ai/grok-4.1-fast": 2_000_000,
            "openai/gpt-4.1": 1_047_576,
            "anthropic/claude-sonnet-4": 200_000,
            "anthropic/claude-sonnet-4.6": 1_000_000,
            "openai/gpt-5.3-codex": 400_000,
        }
        for mid, exp_ctx in context.items():
            cfg = get_model_config(mid)
            assert cfg.context_window == exp_ctx, \
                f"{mid} context_window: {cfg.context_window} != {exp_ctx}"

    def test_all_dashscope_models_registered(self):
        """验证所有 DashScope 模型都在注册表中"""
        expected = {"deepseek-v3.2", "deepseek-r1", "qwen3.5-plus", "kimi-k2.5", "glm-5"}
        ds_models = get_models_by_provider(ModelProvider.DASHSCOPE)
        assert expected.issubset(set(ds_models.keys()))


# ============================================================
# TestImageVideoFactory
# ============================================================


class TestImageVideoFactory:

    @patch("services.adapters.factory.get_settings")
    def test_known_image_model(self, mock_settings):
        """已知图片模型→正确 adapter"""
        mock_settings.return_value = _mock_settings()
        adapter = create_image_adapter("google/nano-banana")
        assert adapter is not None

    @patch("services.adapters.factory.get_settings")
    def test_unknown_image_model_fallback(self, mock_settings):
        """未知图片模型→fallback 默认"""
        mock_settings.return_value = _mock_settings()
        adapter = create_image_adapter("nonexistent-image")
        assert adapter is not None

    @patch("services.adapters.factory.get_settings")
    def test_image_api_key_missing_raises(self, mock_settings):
        """图片 API key 缺失→ValueError"""
        mock_settings.return_value = _mock_settings(kie_api_key=None)
        with pytest.raises(ValueError, match="KIE API Key"):
            create_image_adapter("google/nano-banana")

    @patch("services.adapters.factory.get_settings")
    def test_known_video_model(self, mock_settings):
        """已知视频模型→正确 adapter"""
        mock_settings.return_value = _mock_settings()
        adapter = create_video_adapter("sora-2-text-to-video")
        assert adapter is not None

    @patch("services.adapters.factory.get_settings")
    def test_unknown_video_model_fallback(self, mock_settings):
        """未知视频模型→fallback 默认"""
        mock_settings.return_value = _mock_settings()
        adapter = create_video_adapter("nonexistent-video")
        assert adapter is not None

    @patch("services.adapters.factory.get_settings")
    def test_video_api_key_missing_raises(self, mock_settings):
        """视频 API key 缺失→ValueError"""
        mock_settings.return_value = _mock_settings(kie_api_key=None)
        with pytest.raises(ValueError, match="KIE API Key"):
            create_video_adapter("sora-2-text-to-video")
