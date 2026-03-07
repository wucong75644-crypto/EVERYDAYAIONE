"""
IntentRouter route_retry 单元测试

覆盖：
- 千问返回有效替代模型
- 千问调用 give_up → 确定性兜底
- 千问主模型异常 → 降级到备用模型
- 千问全挂 → 确定性兜底取同类型下一个未试模型
- 所有同类型模型都已失败 → 返回 None
- failed_models 过滤验证
- RetryContext 基本逻辑
- build_retry_tools 过滤逻辑
- get_remaining_models 优先级排序
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType
from services.intent_router import IntentRouter, RetryContext, RoutingDecision
from config.smart_model_config import (
    build_retry_tools,
    get_remaining_models,
    TOOL_TO_TYPE,
)


# ============================================================
# Helpers
# ============================================================


def _mock_settings():
    return MagicMock(
        intent_router_enabled=True,
        dashscope_api_key="sk-test",
        intent_router_model="qwen-plus",
        intent_router_fallback_model="qwen3-flash",
        intent_router_timeout=5.0,
    )


def _make_tool_call_response(tool_name: str, arguments: dict):
    """构建千问返回的 tool_call 响应"""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    }
                }]
            }
        }]
    }
    return resp


# ============================================================
# RetryContext 单元测试
# ============================================================


class TestRetryContext:

    def test_can_retry_true(self):
        ctx = RetryContext(
            is_smart_mode=True,
            original_content="画一张猫",
            generation_type=GenerationType.IMAGE,
        )
        ctx.add_failure("model-a", "timeout")
        assert ctx.can_retry is True

    def test_can_retry_false_max_reached(self):
        ctx = RetryContext(
            is_smart_mode=True,
            original_content="hello",
            generation_type=GenerationType.CHAT,
            max_retries=2,
        )
        ctx.add_failure("model-a", "err1")
        ctx.add_failure("model-b", "err2")
        assert ctx.can_retry is False

    def test_can_retry_false_not_smart(self):
        ctx = RetryContext(
            is_smart_mode=False,
            original_content="hello",
            generation_type=GenerationType.CHAT,
        )
        assert ctx.can_retry is False

    def test_failed_models_list(self):
        ctx = RetryContext(
            is_smart_mode=True,
            original_content="test",
            generation_type=GenerationType.CHAT,
        )
        ctx.add_failure("gemini-3-pro", "error1")
        ctx.add_failure("gemini-3-flash", "error2")
        assert ctx.failed_models == ["gemini-3-pro", "gemini-3-flash"]


# ============================================================
# build_retry_tools 单元测试
# ============================================================


class TestBuildRetryTools:

    def test_filters_failed_models(self):
        """已失败的模型应从 enum 中移除"""
        tools = build_retry_tools(GenerationType.CHAT, ["gemini-3-pro"])
        # 找到 text_chat 工具
        chat_tool = None
        for t in tools:
            if t["function"]["name"] == "text_chat":
                chat_tool = t
                break
        assert chat_tool is not None
        model_enum = chat_tool["function"]["parameters"]["properties"]["model"]["enum"]
        assert "gemini-3-pro" not in model_enum
        assert "gemini-3-flash" in model_enum

    def test_always_includes_give_up(self):
        """必须包含 give_up 工具"""
        tools = build_retry_tools(GenerationType.IMAGE, [])
        tool_names = [t["function"]["name"] for t in tools]
        assert "give_up" in tool_names

    def test_all_models_failed_only_give_up(self):
        """所有模型都失败时，只有 give_up 工具（同类型工具被过滤）"""
        all_chat_models = ["gemini-3-pro", "gemini-3-flash"]
        tools = build_retry_tools(GenerationType.CHAT, all_chat_models)
        tool_names = [t["function"]["name"] for t in tools]
        assert "text_chat" not in tool_names
        assert "give_up" in tool_names

    def test_only_same_type_tools(self):
        """重试只包含同类型的工具"""
        tools = build_retry_tools(GenerationType.IMAGE, [])
        tool_names = [t["function"]["name"] for t in tools]
        assert "generate_image" in tool_names
        assert "text_chat" not in tool_names
        assert "generate_video" not in tool_names


# ============================================================
# get_remaining_models 单元测试
# ============================================================


class TestGetRemainingModels:

    def test_returns_unfailed_models(self):
        remaining = get_remaining_models(GenerationType.CHAT, ["gemini-3-pro"])
        assert "gemini-3-pro" not in remaining
        assert "gemini-3-flash" in remaining

    def test_all_failed_returns_empty(self):
        all_chat = ["gemini-3-pro", "gemini-3-flash"]
        remaining = get_remaining_models(GenerationType.CHAT, all_chat)
        assert remaining == []

    def test_none_failed_returns_all(self):
        remaining = get_remaining_models(GenerationType.IMAGE, [])
        assert len(remaining) >= 2  # nano-banana-pro, google/nano-banana, etc.

    def test_priority_order(self):
        """模型应按配置中的优先级顺序返回"""
        remaining = get_remaining_models(GenerationType.IMAGE, [])
        assert remaining[0] == "google/nano-banana"  # priority 1


# ============================================================
# IntentRouter.route_retry 单元测试
# ============================================================


class TestRouteRetry:

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_returns_new_model_on_success(self, router):
        """千问返回有效替代模型 → 返回 RoutingDecision"""
        mock_response = _make_tool_call_response("text_chat", {
            "system_prompt": "你是助手",
            "model": "gemini-3-flash",
        })
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        router._client = mock_client

        with patch("core.config.get_settings", return_value=_mock_settings()):
            result = await router.route_retry(
                original_content="你好",
                generation_type=GenerationType.CHAT,
                failed_models=["gemini-3-pro"],
                error_message="timeout",
            )

        assert result is not None
        assert result.recommended_model == "gemini-3-flash"
        assert result.routed_by == "model"

    @pytest.mark.asyncio
    async def test_give_up_triggers_deterministic_fallback(self, router):
        """千问调用 give_up → 走确定性兜底"""
        mock_response = _make_tool_call_response("give_up", {
            "reason": "没有更好的选择",
        })
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        router._client = mock_client

        with patch("core.config.get_settings", return_value=_mock_settings()):
            result = await router.route_retry(
                original_content="画猫",
                generation_type=GenerationType.IMAGE,
                failed_models=["nano-banana-pro"],
                error_message="server error",
            )

        # 应走确定性兜底，返回下一个未试模型
        assert result is not None
        assert result.recommended_model != "nano-banana-pro"
        assert result.routed_by == "deterministic_fallback"

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_succeeds(self, router):
        """千问主模型异常 → 降级到备用模型成功"""
        call_count = 0
        success_response = _make_tool_call_response("text_chat", {
            "system_prompt": "你是助手",
            "model": "gemini-3-flash",
        })

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("primary model timeout")
            return success_response

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = mock_post
        router._client = mock_client

        with patch("core.config.get_settings", return_value=_mock_settings()):
            result = await router.route_retry(
                original_content="你好",
                generation_type=GenerationType.CHAT,
                failed_models=["gemini-3-pro"],
                error_message="timeout",
            )

        assert call_count == 2
        assert result is not None
        assert result.recommended_model == "gemini-3-flash"

    @pytest.mark.asyncio
    async def test_all_router_models_fail_deterministic_fallback(self, router):
        """千问主+备用都失败 → 确定性兜底"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=Exception("all failed"))
        router._client = mock_client

        with patch("core.config.get_settings", return_value=_mock_settings()):
            result = await router.route_retry(
                original_content="你好",
                generation_type=GenerationType.CHAT,
                failed_models=["gemini-3-pro"],
                error_message="timeout",
            )

        assert result is not None
        assert result.recommended_model == "gemini-3-flash"
        assert result.routed_by == "deterministic_fallback"

    @pytest.mark.asyncio
    async def test_all_models_exhausted_returns_none(self, router):
        """所有同类型模型都已失败 → 返回 None"""
        all_chat_models = ["gemini-3-pro", "gemini-3-flash"]
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=Exception("error"))
        router._client = mock_client

        with patch("core.config.get_settings", return_value=_mock_settings()):
            result = await router.route_retry(
                original_content="你好",
                generation_type=GenerationType.CHAT,
                failed_models=all_chat_models,
                error_message="all failed",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_no_api_key_uses_deterministic_fallback(self, router):
        """无 API Key → 直接走确定性兜底"""
        settings = _mock_settings()
        settings.dashscope_api_key = ""

        with patch("core.config.get_settings", return_value=settings):
            result = await router.route_retry(
                original_content="画猫",
                generation_type=GenerationType.IMAGE,
                failed_models=["nano-banana-pro"],
                error_message="error",
            )

        assert result is not None
        assert result.routed_by == "deterministic_fallback"

    @pytest.mark.asyncio
    async def test_image_retry_returns_image_model(self, router):
        """图片类型重试应返回图片模型"""
        mock_response = _make_tool_call_response("generate_image", {
            "prompt": "a cat",
            "model": "google/nano-banana",
        })
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        router._client = mock_client

        with patch("core.config.get_settings", return_value=_mock_settings()):
            result = await router.route_retry(
                original_content="画一只猫",
                generation_type=GenerationType.IMAGE,
                failed_models=["nano-banana-pro"],
                error_message="provider error",
            )

        assert result is not None
        assert result.recommended_model == "google/nano-banana"
        assert result.generation_type == GenerationType.IMAGE
