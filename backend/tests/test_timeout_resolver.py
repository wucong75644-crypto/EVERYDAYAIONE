"""超时分级解析器测试"""

from unittest.mock import MagicMock, patch

import pytest

from schemas.message import GenerationType
from services.timeout_resolver import is_thinking_model, resolve_stream_timeout


# ============================================================
# is_thinking_model 测试
# ============================================================


class TestIsThinkingModel:
    """推理模型白名单判断"""

    def test_deepseek_r1_is_thinking(self):
        assert is_thinking_model("deepseek-r1") is True

    def test_o4_mini_is_thinking(self):
        assert is_thinking_model("openai/o4-mini") is True

    def test_gpt_5_4_pro_is_thinking(self):
        assert is_thinking_model("openai/gpt-5.4-pro") is True

    def test_regular_models_not_thinking(self):
        assert is_thinking_model("qwen3.5-plus") is False
        assert is_thinking_model("gemini-3-flash") is False
        assert is_thinking_model("deepseek-v3.2") is False

    def test_unknown_model_not_thinking(self):
        assert is_thinking_model("nonexistent-model") is False


# ============================================================
# resolve_stream_timeout 测试
# ============================================================


def _mock_settings(**overrides):
    """构造 mock Settings"""
    defaults = {
        "chat_stream_timeout": 60.0,
        "chat_thinking_timeout": 120.0,
        "image_generation_timeout": 180.0,
        "video_generation_timeout": 600.0,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestResolveStreamTimeout:
    """超时解析逻辑"""

    @patch("core.config.get_settings")
    def test_chat_regular_model(self, mock_get):
        mock_get.return_value = _mock_settings()
        assert resolve_stream_timeout("qwen3.5-plus") == 60.0

    @patch("core.config.get_settings")
    def test_chat_thinking_model(self, mock_get):
        mock_get.return_value = _mock_settings()
        assert resolve_stream_timeout("deepseek-r1") == 120.0

    @patch("core.config.get_settings")
    def test_chat_default_generation_type(self, mock_get):
        """不传 generation_type 默认按 CHAT 处理"""
        mock_get.return_value = _mock_settings()
        assert resolve_stream_timeout("gemini-3-pro") == 60.0

    @patch("core.config.get_settings")
    def test_image_generation(self, mock_get):
        mock_get.return_value = _mock_settings()
        result = resolve_stream_timeout("any-model", GenerationType.IMAGE)
        assert result == 180.0

    @patch("core.config.get_settings")
    def test_video_generation(self, mock_get):
        mock_get.return_value = _mock_settings()
        result = resolve_stream_timeout("any-model", GenerationType.VIDEO)
        assert result == 600.0

    @patch("core.config.get_settings")
    def test_custom_timeout_from_config(self, mock_get):
        """配置值覆盖默认值"""
        mock_get.return_value = _mock_settings(chat_stream_timeout=30.0)
        assert resolve_stream_timeout("qwen3.5-plus") == 30.0

    @patch("core.config.get_settings")
    def test_image_ignores_model_type(self, mock_get):
        """图片类型时无论模型是否是推理型，都用 image_generation_timeout"""
        mock_get.return_value = _mock_settings()
        result = resolve_stream_timeout("deepseek-r1", GenerationType.IMAGE)
        assert result == 180.0
