"""
IntentRouter 单元测试

覆盖：工具解析、降级链、边界场景、搜索执行
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import MockSupabaseClient

from schemas.message import ContentPart, GenerationType, TextPart, ImagePart
from services.intent_router import (
    IntentRouter,
    RoutingDecision,
    ROUTER_TOOLS,
)
from config.smart_model_config import TOOL_TO_TYPE as _TOOL_TO_TYPE


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def router():
    return IntentRouter()


def _make_text_content(text: str) -> list[ContentPart]:
    return [TextPart(text=text)]


# ============================================================
# RoutingDecision 数据结构
# ============================================================


class TestRoutingDecision:
    def test_default_values(self):
        d = RoutingDecision(generation_type=GenerationType.CHAT)
        assert d.generation_type == GenerationType.CHAT
        assert d.system_prompt is None
        assert d.tool_params == {}
        assert d.search_query is None
        assert d.raw_tool_name == "text_chat"
        assert d.routed_by == "keyword"

    def test_custom_values(self):
        d = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            system_prompt="你是设计师",
            tool_params={"prompt": "a cat"},
            raw_tool_name="generate_image",
            routed_by="model",
        )
        assert d.generation_type == GenerationType.IMAGE
        assert d.system_prompt == "你是设计师"
        assert d.tool_params["prompt"] == "a cat"


# ============================================================
# _parse_response 测试
# ============================================================


class TestParseResponse:
    def test_text_chat_tool_call(self, router):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "text_chat",
                            "arguments": json.dumps({"system_prompt": "你是翻译专家"}),
                        }
                    }]
                }
            }]
        }
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt == "你是翻译专家"
        assert result.raw_tool_name == "text_chat"
        assert result.routed_by == "model"

    def test_generate_image_tool_call(self, router):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "generate_image",
                            "arguments": json.dumps({
                                "prompt": "a cute cat",
                                "edit_mode": False,
                            }),
                        }
                    }]
                }
            }]
        }
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.IMAGE
        assert result.tool_params["prompt"] == "a cute cat"
        assert result.tool_params["edit_mode"] is False

    def test_generate_video_tool_call(self, router):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "generate_video",
                            "arguments": json.dumps({"prompt": "ocean waves"}),
                        }
                    }]
                }
            }]
        }
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.VIDEO

    def test_web_search_tool_call(self, router):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "web_search",
                            "arguments": json.dumps({
                                "search_query": "iPhone 最新价格",
                                "system_prompt": "你是数码产品分析师",
                            }),
                        }
                    }]
                }
            }]
        }
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.CHAT
        assert result.search_query == "iPhone 最新价格"
        assert result.system_prompt == "你是数码产品分析师"

    def test_no_tool_calls_defaults_to_chat(self, router):
        data = {"choices": [{"message": {"content": "Hello"}}]}
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.CHAT
        assert result.routed_by == "model_no_tool"

    def test_empty_choices(self, router):
        result = router._parse_response({"choices": []})
        assert result is None

    def test_unknown_tool_name(self, router):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "unknown_tool",
                            "arguments": "{}",
                        }
                    }]
                }
            }]
        }
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.CHAT

    def test_invalid_json_arguments(self, router):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "text_chat",
                            "arguments": "not json",
                        }
                    }]
                }
            }]
        }
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt is None

    def test_missing_arguments_field(self, router):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {"name": "text_chat"}
                    }]
                }
            }]
        }
        result = router._parse_response(data)
        assert result.generation_type == GenerationType.CHAT


# ============================================================
# _keyword_fallback 测试
# ============================================================


class TestKeywordFallback:
    def test_chat_text(self, router):
        content = _make_text_content("你好，请帮我翻译")
        result = router._keyword_fallback(content)
        assert result.generation_type == GenerationType.CHAT
        assert result.routed_by == "keyword"

    def test_image_keyword(self, router):
        content = _make_text_content("画一只猫")
        result = router._keyword_fallback(content)
        assert result.generation_type == GenerationType.IMAGE

    def test_video_keyword(self, router):
        content = _make_text_content("生成视频：海浪")
        result = router._keyword_fallback(content)
        assert result.generation_type == GenerationType.VIDEO


# ============================================================
# _extract_text 测试
# ============================================================


class TestExtractText:
    def test_single_text(self, router):
        content = [TextPart(text="hello")]
        assert router._extract_text(content) == "hello"

    def test_multiple_text_parts(self, router):
        content = [TextPart(text="hello"), TextPart(text="world")]
        assert router._extract_text(content) == "hello world"

    def test_mixed_content(self, router):
        content = [
            TextPart(text="分析这张图"),
            ImagePart(url="http://img.jpg"),
        ]
        assert router._extract_text(content) == "分析这张图"

    def test_empty_content(self, router):
        assert router._extract_text([]) == ""


# ============================================================
# route() 集成测试（mock HTTP）
# ============================================================


class TestRoute:
    @pytest.mark.asyncio
    async def test_disabled_router_uses_keyword(self, router):
        """路由器禁用时回退到关键词"""
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                intent_router_enabled=False,
            )
            content = _make_text_content("画一只猫")
            result = await router.route(content, "user-1", "conv-1")
            assert result.generation_type == GenerationType.IMAGE
            assert result.routed_by == "keyword"

    @pytest.mark.asyncio
    async def test_no_api_key_uses_keyword(self, router):
        """API key 未配置时回退到关键词"""
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                intent_router_enabled=True,
                dashscope_api_key=None,
            )
            content = _make_text_content("你好")
            result = await router.route(content, "user-1", "conv-1")
            assert result.routed_by == "keyword"

    @pytest.mark.asyncio
    async def test_empty_text_skips_routing(self, router):
        """空文本跳过路由"""
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                intent_router_enabled=True,
                dashscope_api_key="sk-test",
            )
            content = _make_text_content("")
            result = await router.route(content, "user-1", "conv-1")
            assert result.routed_by == "skip_empty"

    @pytest.mark.asyncio
    async def test_successful_model_routing(self, router):
        """模型成功路由"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "text_chat",
                            "arguments": json.dumps({"system_prompt": "你是助手"}),
                        }
                    }]
                }
            }]
        }

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                intent_router_enabled=True,
                dashscope_api_key="sk-test",
                intent_router_model="qwen-plus",
                intent_router_fallback_model="qwen3-flash",
                intent_router_timeout=5.0,
            )

            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            router._client = mock_client

            content = _make_text_content("帮我翻译这段话")
            result = await router.route(content, "user-1", "conv-1")

            assert result.generation_type == GenerationType.CHAT
            assert result.system_prompt == "你是助手"
            assert result.routed_by == "model"

    @pytest.mark.asyncio
    async def test_fallback_on_model_failure(self, router):
        """主模型失败时降级到备用模型"""
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("API error")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "function": {
                                "name": "text_chat",
                                "arguments": json.dumps({"system_prompt": "备用"}),
                            }
                        }]
                    }
                }]
            }
            return resp

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                intent_router_enabled=True,
                dashscope_api_key="sk-test",
                intent_router_model="qwen-plus",
                intent_router_fallback_model="qwen3-flash",
                intent_router_timeout=5.0,
            )

            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = mock_post
            router._client = mock_client

            content = _make_text_content("你好")
            result = await router.route(content, "user-1", "conv-1")

            assert result.system_prompt == "备用"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_models_fail_uses_keyword(self, router):
        """所有模型失败时回退到关键词"""
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                intent_router_enabled=True,
                dashscope_api_key="sk-test",
                intent_router_model="qwen-plus",
                intent_router_fallback_model="qwen3-flash",
                intent_router_timeout=5.0,
            )

            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(side_effect=Exception("All fail"))
            router._client = mock_client

            content = _make_text_content("画一只猫")
            result = await router.route(content, "user-1", "conv-1")

            assert result.generation_type == GenerationType.IMAGE
            assert result.routed_by == "keyword"


# ============================================================
# execute_search 测试
# ============================================================


class TestExecuteSearch:
    @pytest.mark.asyncio
    async def test_successful_search(self, router):
        """搜索成功返回结果"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": "iPhone 16 售价 6999 元起"}
            }]
        }

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                dashscope_api_key="sk-test",
                intent_router_model="qwen-plus",
            )

            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            router._client = mock_client

            result = await router.execute_search(
                query="iPhone价格",
                user_text="最新iPhone多少钱",
                system_prompt="你是数码分析师",
            )

            assert result == "iPhone 16 售价 6999 元起"

    @pytest.mark.asyncio
    async def test_search_failure_returns_none(self, router):
        """搜索失败返回 None"""
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                dashscope_api_key="sk-test",
                intent_router_model="qwen-plus",
            )

            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(side_effect=Exception("timeout"))
            router._client = mock_client

            result = await router.execute_search("query", "text")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self, router):
        """无 API key 返回 None"""
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(dashscope_api_key=None)

            result = await router.execute_search("query", "text")
            assert result is None


# ============================================================
# 工具定义完整性
# ============================================================


class TestToolDefinitions:
    def test_all_tools_defined(self):
        names = [t["function"]["name"] for t in ROUTER_TOOLS]
        assert "generate_image" in names
        assert "generate_video" in names
        assert "web_search" in names
        assert "text_chat" in names

    def test_tool_to_type_mapping(self):
        # Agent Loop 新路由工具
        assert _TOOL_TO_TYPE["route_to_image"] == GenerationType.IMAGE
        assert _TOOL_TO_TYPE["route_to_video"] == GenerationType.VIDEO
        assert _TOOL_TO_TYPE["route_to_chat"] == GenerationType.CHAT
        # IntentRouter 旧工具名（向后兼容）
        assert _TOOL_TO_TYPE["generate_image"] == GenerationType.IMAGE
        assert _TOOL_TO_TYPE["generate_video"] == GenerationType.VIDEO
        assert _TOOL_TO_TYPE["web_search"] == GenerationType.CHAT
        assert _TOOL_TO_TYPE["text_chat"] == GenerationType.CHAT


# ============================================================
# _filter_tools_by_breaker 测试
# ============================================================


class TestFilterToolsByBreaker:

    def test_removes_broken_provider_models(self, router):
        """熔断 Provider 的模型从 enum 中移除"""
        from services.adapters.base import ModelProvider

        tools = [{
            "type": "function",
            "function": {
                "name": "text_chat",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "enum": ["model-a", "model-b", "model-c"],
                        },
                    },
                },
            },
        }]

        mock_registry = {
            "model-a": MagicMock(provider=ModelProvider.KIE),
            "model-b": MagicMock(provider=ModelProvider.OPENROUTER),
            "model-c": MagicMock(provider=ModelProvider.KIE),
        }

        def mock_available(provider):
            return provider != ModelProvider.KIE

        with patch("services.circuit_breaker.is_provider_available", mock_available), \
             patch("services.adapters.factory.MODEL_REGISTRY", mock_registry):
            filtered = router._filter_tools_by_breaker(tools)

        enum_values = filtered[0]["function"]["parameters"]["properties"]["model"]["enum"]
        assert enum_values == ["model-b"]

    def test_all_models_broken_removes_tool(self, router):
        """所有模型都被熔断时整个工具被移除"""
        from services.adapters.base import ModelProvider

        tools = [{
            "type": "function",
            "function": {
                "name": "text_chat",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "enum": ["model-a"],
                        },
                    },
                },
            },
        }]

        mock_registry = {
            "model-a": MagicMock(provider=ModelProvider.KIE),
        }

        with patch("services.circuit_breaker.is_provider_available", return_value=False), \
             patch("services.adapters.factory.MODEL_REGISTRY", mock_registry):
            filtered = router._filter_tools_by_breaker(tools)

        assert len(filtered) == 0

    def test_does_not_mutate_original_tools(self, router):
        """过滤不修改原始工具列表"""
        from services.adapters.base import ModelProvider

        tools = [{
            "type": "function",
            "function": {
                "name": "text_chat",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "enum": ["model-a", "model-b"],
                        },
                    },
                },
            },
        }]

        mock_registry = {
            "model-a": MagicMock(provider=ModelProvider.KIE),
            "model-b": MagicMock(provider=ModelProvider.OPENROUTER),
        }

        with patch("services.circuit_breaker.is_provider_available", return_value=False), \
             patch("services.adapters.factory.MODEL_REGISTRY", mock_registry):
            router._filter_tools_by_breaker(tools)

        # 原始 tools 未被修改
        original_enum = tools[0]["function"]["parameters"]["properties"]["model"]["enum"]
        assert original_enum == ["model-a", "model-b"]
