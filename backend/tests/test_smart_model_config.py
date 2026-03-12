"""
智能模型配置单元测试

覆盖：JSON 加载、模型映射、ROUTER_TOOLS 枚举包含 OpenRouter 模型、
      重试工具过滤、默认模型常量
"""

from unittest.mock import MagicMock, patch

import pytest

from config.smart_model_config import (
    SMART_CONFIG,
    MODEL_TO_GEN_TYPE,
    AUTO_MODEL_DEFAULTS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
    ROUTER_TOOLS,
    TOOL_TO_TYPE,
    build_router_tools,
    build_retry_tools,
    get_remaining_models,
    get_image_to_video_model,
    _get_model_enum,
    _get_model_desc,
)
from schemas.message import GenerationType


# ============================================================
# TestConfigLoading
# ============================================================


class TestConfigLoading:

    def test_smart_config_loaded(self):
        """smart_models.json 成功加载"""
        assert SMART_CONFIG is not None
        assert isinstance(SMART_CONFIG, dict)

    def test_has_all_categories(self):
        """包含 chat/image/video/web_search 四个分类"""
        for cat in ("chat", "image", "video", "web_search"):
            assert cat in SMART_CONFIG, f"缺少分类: {cat}"
            assert "models" in SMART_CONFIG[cat]
            assert "default" in SMART_CONFIG[cat]

    def test_chat_models_include_openrouter(self):
        """chat 分类包含 OpenRouter 模型"""
        chat_ids = [m["id"] for m in SMART_CONFIG["chat"]["models"]]
        openrouter_ids = [
            "openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/o4-mini",
            "anthropic/claude-sonnet-4", "x-ai/grok-4.1-fast",
            "openai/gpt-5.4", "openai/gpt-5.4-pro", "openai/gpt-5.3-codex",
            "google/gemini-3.1-pro-preview",
            "anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.6",
        ]
        for mid in openrouter_ids:
            assert mid in chat_ids, f"chat 分类缺少模型: {mid}"

    def test_each_model_has_required_fields(self):
        """每个模型条目包含 id/description/priority"""
        for cat in ("chat", "image", "video", "web_search"):
            for m in SMART_CONFIG[cat]["models"]:
                assert "id" in m, f"模型缺少 id: {m}"
                assert "description" in m, f"模型 {m['id']} 缺少 description"
                assert "priority" in m, f"模型 {m['id']} 缺少 priority"

    def test_model_priorities_unique_per_category(self):
        """同一分类内 priority 不重复"""
        for cat in ("chat", "image", "video", "web_search"):
            priorities = [m["priority"] for m in SMART_CONFIG[cat]["models"]]
            assert len(priorities) == len(set(priorities)), \
                f"{cat} 分类存在重复 priority: {priorities}"


# ============================================================
# TestModelMapping
# ============================================================


class TestModelMapping:

    def test_openrouter_models_in_mapping(self):
        """OpenRouter 模型映射到 GenerationType.CHAT"""
        for mid in ("openai/gpt-4.1", "anthropic/claude-sonnet-4.6", "x-ai/grok-4.1-fast"):
            assert mid in MODEL_TO_GEN_TYPE, f"映射缺少: {mid}"
            assert MODEL_TO_GEN_TYPE[mid] == GenerationType.CHAT

    def test_dashscope_models_in_mapping(self):
        """DashScope 模型也在映射中"""
        for mid in ("deepseek-v3.2", "qwen3.5-plus"):
            assert mid in MODEL_TO_GEN_TYPE

    def test_image_video_models_in_mapping(self):
        """图片/视频模型映射正确"""
        assert MODEL_TO_GEN_TYPE["google/nano-banana"] == GenerationType.IMAGE
        assert MODEL_TO_GEN_TYPE["sora-2-text-to-video"] == GenerationType.VIDEO


# ============================================================
# TestDefaults
# ============================================================


class TestDefaults:

    def test_default_chat_model(self):
        assert DEFAULT_CHAT_MODEL == "qwen3.5-plus"

    def test_default_image_model(self):
        assert DEFAULT_IMAGE_MODEL == "google/nano-banana"

    def test_default_video_model(self):
        assert DEFAULT_VIDEO_MODEL == "sora-2-text-to-video"

    def test_auto_defaults_mapping(self):
        assert GenerationType.CHAT in AUTO_MODEL_DEFAULTS
        assert GenerationType.IMAGE in AUTO_MODEL_DEFAULTS
        assert GenerationType.VIDEO in AUTO_MODEL_DEFAULTS


# ============================================================
# TestRouterTools
# ============================================================


class TestRouterTools:

    def test_router_tools_has_four_tools(self):
        """ROUTER_TOOLS 包含 4 个工具"""
        names = [t["function"]["name"] for t in ROUTER_TOOLS]
        assert set(names) == {"generate_image", "generate_video", "web_search", "text_chat"}

    def test_text_chat_enum_includes_openrouter(self):
        """text_chat 工具的 model enum 包含 OpenRouter 模型"""
        text_chat = next(t for t in ROUTER_TOOLS if t["function"]["name"] == "text_chat")
        model_enum = text_chat["function"]["parameters"]["properties"]["model"]["enum"]
        for mid in ("openai/gpt-4.1", "anthropic/claude-sonnet-4.6", "openai/gpt-5.4"):
            assert mid in model_enum, f"text_chat enum 缺少: {mid}"

    def test_text_chat_enum_includes_dashscope(self):
        """text_chat 工具的 model enum 也包含 DashScope 模型"""
        text_chat = next(t for t in ROUTER_TOOLS if t["function"]["name"] == "text_chat")
        model_enum = text_chat["function"]["parameters"]["properties"]["model"]["enum"]
        assert "qwen3.5-plus" in model_enum
        assert "deepseek-v3.2" in model_enum

    def test_build_router_tools_returns_list(self):
        """build_router_tools() 返回列表"""
        tools = build_router_tools()
        assert isinstance(tools, list)
        assert len(tools) == 4


# ============================================================
# TestRetryTools
# ============================================================


class TestRetryTools:

    def test_retry_excludes_failed_model(self):
        """重试工具过滤已失败模型"""
        retry = build_retry_tools(GenerationType.CHAT, ["qwen3.5-plus"])
        text_chat = next(
            (t for t in retry if t["function"]["name"] == "text_chat"), None
        )
        assert text_chat is not None
        model_enum = text_chat["function"]["parameters"]["properties"]["model"]["enum"]
        assert "qwen3.5-plus" not in model_enum
        # OpenRouter 模型仍在
        assert "openai/gpt-4.1" in model_enum

    def test_retry_includes_give_up(self):
        """重试工具包含 give_up"""
        retry = build_retry_tools(GenerationType.CHAT, [])
        names = [t["function"]["name"] for t in retry]
        assert "give_up" in names

    def test_get_remaining_models_excludes_failed(self):
        """get_remaining_models 过滤失败模型"""
        remaining = get_remaining_models(
            GenerationType.CHAT, ["qwen3.5-plus", "deepseek-v3.2"]
        )
        assert "qwen3.5-plus" not in remaining
        assert "deepseek-v3.2" not in remaining
        # OpenRouter 模型仍在
        assert "openai/gpt-4.1" in remaining

    def test_get_remaining_models_preserves_order(self):
        """get_remaining_models 保持优先级顺序"""
        remaining = get_remaining_models(GenerationType.CHAT, [])
        assert len(remaining) > 0
        # 第一个应该是 priority=1 的模型（qwen3.5-plus）
        assert remaining[0] == "qwen3.5-plus"


# ============================================================
# TestHelperFunctions
# ============================================================


class TestHelperFunctions:

    def test_get_image_to_video_model_returns_string(self):
        """get_image_to_video_model 返回有效模型 ID"""
        model = get_image_to_video_model()
        assert isinstance(model, str)
        assert len(model) > 0

    def test_get_image_to_video_model_in_video_models(self):
        """返回的模型在 video 分类中"""
        model = get_image_to_video_model()
        video_ids = [m["id"] for m in SMART_CONFIG["video"]["models"]]
        assert model in video_ids

    def test_get_model_enum_chat(self):
        """_get_model_enum('chat') 返回 chat 模型列表"""
        enums = _get_model_enum("chat")
        assert isinstance(enums, list)
        assert len(enums) > 0
        assert "qwen3.5-plus" in enums

    def test_get_model_enum_nonexistent(self):
        """_get_model_enum 不存在的分类→空列表"""
        enums = _get_model_enum("nonexistent")
        assert enums == []

    def test_get_model_desc_chat(self):
        """_get_model_desc('chat') 返回描述文本"""
        desc = _get_model_desc("chat")
        assert isinstance(desc, str)
        assert len(desc) > 0
        # 应包含模型 ID 和描述
        assert "qwen3.5-plus" in desc

    def test_get_model_desc_nonexistent(self):
        """_get_model_desc 不存在的分类→空字符串"""
        desc = _get_model_desc("nonexistent")
        assert desc == ""


# ============================================================
# TestToolToType
# ============================================================


class TestToolToType:

    def test_new_routing_tools_mapped(self):
        """Agent Loop 新路由工具在映射中"""
        assert TOOL_TO_TYPE["route_to_image"] == GenerationType.IMAGE
        assert TOOL_TO_TYPE["route_to_video"] == GenerationType.VIDEO
        assert TOOL_TO_TYPE["route_to_chat"] == GenerationType.CHAT

    def test_legacy_tools_still_mapped(self):
        """IntentRouter 旧工具名仍在映射中（向后兼容）"""
        assert TOOL_TO_TYPE["generate_image"] == GenerationType.IMAGE
        assert TOOL_TO_TYPE["generate_video"] == GenerationType.VIDEO
        assert TOOL_TO_TYPE["text_chat"] == GenerationType.CHAT
        assert TOOL_TO_TYPE["web_search"] == GenerationType.CHAT


# ============================================================
# TestGetAvailableModelSet — 熔断集成过滤
# ============================================================


class TestGetAvailableModelSet:

    @patch("services.circuit_breaker.is_provider_available", return_value=True)
    @patch("services.adapters.factory.VIDEO_MODEL_REGISTRY", {
        "sora-2-text-to-video": {"provider": "kie"},
    })
    @patch("services.adapters.factory.IMAGE_MODEL_REGISTRY", {
        "google/nano-banana": {"provider": "kie"},
    })
    @patch("services.adapters.factory.MODEL_REGISTRY", {
        "qwen3.5-plus": MagicMock(provider="dashscope"),
    })
    def test_all_available_returns_all(self, mock_avail):
        """所有 Provider 正常时返回全部模型（排除 failed_models）"""
        from config.smart_model_config import _get_available_model_set

        result = _get_available_model_set(["qwen3.5-plus"])
        # qwen3.5-plus 在 failed_models 中，应被排除
        assert "qwen3.5-plus" not in result

    @patch("services.circuit_breaker.is_provider_available")
    @patch("services.adapters.factory.VIDEO_MODEL_REGISTRY", {})
    @patch("services.adapters.factory.IMAGE_MODEL_REGISTRY", {})
    @patch("services.adapters.factory.MODEL_REGISTRY", {
        "qwen3.5-plus": MagicMock(provider="dashscope"),
        "gemini-3-pro": MagicMock(provider="kie"),
    })
    def test_broken_provider_filtered_out(self, mock_avail):
        """熔断 Provider 的模型被过滤"""
        from config.smart_model_config import _get_available_model_set

        def side_effect(provider):
            return provider != "kie"

        mock_avail.side_effect = side_effect
        result = _get_available_model_set([])
        # kie 熔断 → gemini-3-pro 被过滤（如果在 SMART_CONFIG 中）
        assert "gemini-3-pro" not in result

    def test_import_failure_returns_all_non_failed(self):
        """熔断器导入失败时返回所有非失败模型"""
        from config.smart_model_config import _get_available_model_set

        with patch("services.circuit_breaker.is_provider_available", side_effect=ImportError):
            result = _get_available_model_set(["qwen3.5-plus"])

        # 导入失败时降级：返回除 failed 之外的全部模型
        assert "qwen3.5-plus" not in result
        # 其他模型应该在
        assert len(result) > 0
