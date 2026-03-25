"""
Agent Loop 基础设施 + 信号 单元测试

覆盖：模型校验、知识记录、HTTP 客户端管理、资源释放、
      provider 模型选择、路由信号记录、慢速工具超时、
      意图学习上下文、意图学习检查
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_types import AgentResult, AgentGuardrails
from services.agent_loop import AgentLoop


# ============================================================
# Helpers
# ============================================================

def _make_loop() -> AgentLoop:
    """创建 AgentLoop 实例（mock db）"""
    return AgentLoop(db=MagicMock(), user_id="u1", conversation_id="c1")


def _make_tool_call(name: str, arguments: dict, tc_id: str = "tc_1") -> dict:
    return {
        "id": tc_id,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _make_brain_response(
    tool_calls: list | None = None,
    content: str | None = None,
    usage: dict | None = None,
) -> dict:
    """构造模拟的大脑 API 响应"""
    message = {}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if content:
        message["content"] = content
    return {
        "choices": [{"message": message}],
        "usage": usage or {"total_tokens": 100},
    }


# ============================================================
# TestValidateRoutingModel — 路由模型校验
# ============================================================

class TestValidateRoutingModel:

    def test_non_chat_route_passes(self):
        """非 route_to_chat 工具→不校验"""
        loop = _make_loop()
        assert loop._validate_routing_model(
            "route_to_image", {"model": "google/nano-banana"},
        ) is None

    def test_image_mismatch_returns_warning(self):
        """用户有图片但模型不支持→返回警告"""
        loop = _make_loop()
        loop._has_image = True
        result = loop._validate_routing_model(
            "route_to_chat",
            {"model": "deepseek-v3.2"},
        )
        assert result is not None
        assert "不支持图片" in result

    def test_search_mismatch_returns_warning(self):
        """需要搜索但模型不支持→返回警告"""
        loop = _make_loop()
        loop._has_image = False
        result = loop._validate_routing_model(
            "route_to_chat",
            {"model": "qwen3.5-plus", "needs_google_search": True},
        )
        assert result is not None
        assert "不支持联网搜索" in result

    def test_valid_choice_passes(self):
        """能力匹配→返回 None"""
        loop = _make_loop()
        loop._has_image = False
        assert loop._validate_routing_model(
            "route_to_chat",
            {"model": "qwen3.5-plus"},
        ) is None

    def test_unknown_model_passes(self):
        """不在 chat 列表中的模型→不做校验"""
        loop = _make_loop()
        loop._has_image = True
        assert loop._validate_routing_model(
            "route_to_chat",
            {"model": "unknown-model-xyz"},
        ) is None

    def test_no_has_image_attr_defaults_false(self):
        """_has_image 未设置→默认 False"""
        loop = _make_loop()
        # 不设置 _has_image
        assert loop._validate_routing_model(
            "route_to_chat",
            {"model": "deepseek-v3.2"},
        ) is None


# ============================================================
# TestFireAndForgetKnowledge — 知识记录
# ============================================================

class TestFireAndForgetKnowledge:

    def test_calls_extract_and_save(self):
        """正常调用→create_task 被执行"""
        loop = _make_loop()
        mock_task = MagicMock()
        with patch("services.agent_loop_infra.asyncio.create_task", mock_task), \
             patch(
                 "services.knowledge_extractor.extract_and_save",
                 new_callable=AsyncMock,
             ) as mock_extract:
            loop._fire_and_forget_knowledge(
                task_type="tool_validation", model_id="fake_tool",
                status="failed", error_message="test error",
            )
            mock_task.assert_called_once()

    def test_import_error_silenced(self):
        """import 失败→静默跳过"""
        loop = _make_loop()
        with patch.dict("sys.modules", {"services.knowledge_extractor": None}):
            # 不应抛异常
            loop._fire_and_forget_knowledge(
                task_type="test", model_id="x",
                status="failed", error_message="err",
            )

    def test_model_mismatch_triggers_knowledge(self):
        """模型不匹配→触发知识记录"""
        loop = _make_loop()
        loop._has_image = True
        mock_task = MagicMock()
        with patch("services.agent_loop_infra.asyncio.create_task", mock_task), \
             patch(
                 "services.knowledge_extractor.extract_and_save",
                 new_callable=AsyncMock,
             ), \
             patch(
                 "config.smart_model_config.validate_model_choice",
                 return_value="模型不支持图片",
             ):
            warning = loop._validate_routing_model(
                "route_to_chat", {"model": "test-model"},
            )
            assert warning == "模型不支持图片"
            mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_tool_triggers_knowledge(self):
        """无效工具调用→触发知识记录"""
        loop = _make_loop()
        loop._settings = MagicMock()
        tc = _make_tool_call("hallucinated_tool", {"x": 1})
        tool_results = []
        routing = {}
        guardrails = AgentGuardrails()
        context = []

        mock_task = MagicMock()
        with patch("services.agent_loop_infra.asyncio.create_task", mock_task):
            await loop._process_tool_call(
                tc, 1, guardrails, tool_results, context, routing,
            )
        # schema 验证失败 → 结果包含错误
        assert any(r.get("is_error") for r in tool_results)
        mock_task.assert_called_once()


# ============================================================
# TestGetClient — HTTP 客户端管理
# ============================================================

class TestGetClient:

    @pytest.mark.asyncio
    async def test_creates_new_client(self):
        """首次调用→创建新客户端"""
        loop = _make_loop()
        loop._settings = MagicMock(
            dashscope_base_url="https://api.example.com",
            dashscope_api_key="sk-test",
            agent_loop_timeout=10.0,
        )

        client = await loop._get_client()
        assert client is not None
        assert loop._client is client
        await loop.close()

    @pytest.mark.asyncio
    async def test_reuses_existing_client(self):
        """已有未关闭客户端→复用"""
        loop = _make_loop()
        loop._settings = MagicMock(
            dashscope_base_url="https://api.example.com",
            dashscope_api_key="sk-test",
            agent_loop_timeout=10.0,
        )

        client1 = await loop._get_client()
        client2 = await loop._get_client()
        assert client1 is client2
        await loop.close()

    @pytest.mark.asyncio
    async def test_openrouter_provider_uses_openrouter_config(self):
        """provider=openrouter → 使用 OpenRouter 配置"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="openrouter",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_api_key="or-test-key",
            openrouter_app_title="TestApp",
            agent_loop_timeout=10.0,
        )

        client = await loop._get_client()
        assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
        assert client.headers["authorization"] == "Bearer or-test-key"
        assert client.headers["x-title"] == "TestApp"
        assert client.headers["http-referer"] == "https://everydayai.one"
        await loop.close()

    @pytest.mark.asyncio
    async def test_dashscope_provider_no_extra_headers(self):
        """provider=dashscope → 无 X-Title/HTTP-Referer 头"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="dashscope",
            dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            dashscope_api_key="sk-dash",
            agent_loop_timeout=10.0,
        )

        client = await loop._get_client()
        assert str(client.base_url).rstrip("/") == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert client.headers["authorization"] == "Bearer sk-dash"
        assert "x-title" not in client.headers
        assert "http-referer" not in client.headers
        await loop.close()


# ============================================================
# TestClose — 资源释放
# ============================================================

class TestClose:

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        """无客户端→安全 no-op"""
        loop = _make_loop()
        await loop.close()  # 不抛异常
        assert loop._client is None

    @pytest.mark.asyncio
    async def test_close_releases_client(self):
        """关闭后 _client 置 None"""
        loop = _make_loop()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        loop._client = mock_client

        await loop.close()
        mock_client.aclose.assert_awaited_once()
        assert loop._client is None

    @pytest.mark.asyncio
    async def test_close_skips_already_closed(self):
        """已关闭的客户端→跳过"""
        loop = _make_loop()
        mock_client = AsyncMock()
        mock_client.is_closed = True
        loop._client = mock_client

        await loop.close()
        mock_client.aclose.assert_not_awaited()


# ============================================================
# TestCallBrainModelSelection — provider 模型选择
# ============================================================

class TestCallBrainModelSelection:

    @pytest.mark.asyncio
    async def test_openrouter_uses_openrouter_model(self):
        """provider=openrouter → 使用 agent_loop_openrouter_model"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="openrouter",
            agent_loop_openrouter_model="anthropic/claude-sonnet-4.6",
            agent_loop_model="qwen3.5-plus",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = _make_brain_response(content="hi")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        loop._client = mock_client

        dummy_tools = [{"type": "function", "function": {"name": "test_tool"}}]
        result = await loop._call_brain(
            [{"role": "user", "content": "test"}], tools=dummy_tools,
        )

        call_json = mock_client.post.call_args[1]["json"]
        assert call_json["model"] == "anthropic/claude-sonnet-4.6"

        await loop.close()

    @pytest.mark.asyncio
    async def test_dashscope_uses_dashscope_model(self):
        """provider=dashscope → 使用 agent_loop_model"""
        loop = _make_loop()
        loop._settings = MagicMock(
            agent_loop_provider="dashscope",
            agent_loop_openrouter_model="anthropic/claude-sonnet-4.6",
            agent_loop_model="qwen3.5-plus",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = _make_brain_response(content="hi")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False
        loop._client = mock_client

        dummy_tools = [{"type": "function", "function": {"name": "test_tool"}}]
        result = await loop._call_brain(
            [{"role": "user", "content": "test"}], tools=dummy_tools,
        )

        call_json = mock_client.post.call_args[1]["json"]
        assert call_json["model"] == "qwen3.5-plus"

        await loop.close()

