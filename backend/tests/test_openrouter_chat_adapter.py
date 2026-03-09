"""
OpenRouter Chat 适配器单元测试

覆盖：SSE 流式解析、usage.cost 积分换算、null 防护、错误处理、兜底估算
"""

import json
import math
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.adapters.openrouter.chat_adapter import (
    CREDITS_MARKUP,
    CREDITS_PER_USD,
    OpenRouterAPIError,
    OpenRouterChatAdapter,
)
from services.adapters.base import ModelProvider, StreamChunk


# ============================================================
# Helpers
# ============================================================


def _make_adapter(model: str = "openai/gpt-4.1") -> OpenRouterChatAdapter:
    return OpenRouterChatAdapter(
        api_key="sk-or-test-key",
        model=model,
        base_url="https://openrouter.example.com/api/v1",
        app_title="TestApp",
    )


def _sse_line(data: dict) -> str:
    return f"data: {json.dumps(data)}"


def _make_chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> dict:
    chunk: dict = {
        "choices": [{
            "delta": {"content": content, "role": "assistant"},
            "index": 0,
            "finish_reason": finish_reason,
        }],
        "usage": usage,
        "model": "openai/gpt-4.1",
    }
    return chunk


class MockStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200, error_body: bytes = b""):
        self.status_code = status_code
        self._lines = lines
        self._error_body = error_body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._error_body


def _patch_stream(adapter: OpenRouterChatAdapter, response: MockStreamResponse):
    """给 adapter 注入 mock stream"""
    @asynccontextmanager
    async def mock_stream(*args, **kwargs):
        yield response

    adapter._client = MagicMock()
    adapter._client.is_closed = False
    adapter._client.stream = mock_stream


# ============================================================
# TestInit
# ============================================================


class TestInit:
    def test_stores_config(self):
        adapter = _make_adapter()
        assert adapter._api_key == "sk-or-test-key"
        assert adapter._model_id == "openai/gpt-4.1"
        assert adapter._base_url == "https://openrouter.example.com/api/v1"
        assert adapter._app_title == "TestApp"

    def test_strips_trailing_slash(self):
        adapter = OpenRouterChatAdapter(
            api_key="k", model="m", base_url="https://example.com/v1/"
        )
        assert adapter._base_url == "https://example.com/v1"

    def test_provider(self):
        assert _make_adapter().provider == ModelProvider.OPENROUTER

    def test_supports_streaming(self):
        assert _make_adapter().supports_streaming is True


# ============================================================
# TestStreamChat
# ============================================================


class TestStreamChat:

    @pytest.mark.asyncio
    async def test_normal_stream_yields_content(self):
        """正常流式输出"""
        lines = [
            _sse_line(_make_chunk(content="Hello")),
            "",
            _sse_line(_make_chunk(content=" World")),
            "",
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].content == "Hello"
        assert chunks[1].content == " World"

    @pytest.mark.asyncio
    async def test_usage_cost_converts_to_credits(self):
        """usage.cost（USD）正确换算为积分：ceil(cost × 200) + 1"""
        cost_usd = 0.0035  # $0.0035
        expected_credits = math.ceil(cost_usd * CREDITS_PER_USD) + CREDITS_MARKUP
        # = ceil(0.7) + 1 = 1 + 1 = 2

        lines = [
            _sse_line(_make_chunk(content="hi")),
            "",
            _sse_line({
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "cost": cost_usd,
                },
                "model": "openai/gpt-4.1",
            }),
            "",
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        last = chunks[-1]
        assert last.prompt_tokens == 100
        assert last.completion_tokens == 50
        assert last.credits_consumed == expected_credits

    @pytest.mark.asyncio
    async def test_usage_cost_zero(self):
        """cost=0 时积分 = ceil(0) + 1 = 1"""
        lines = [
            _sse_line({
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "cost": 0.0},
                "model": "openai/gpt-4.1",
            }),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        assert chunks[-1].credits_consumed == CREDITS_MARKUP  # 0 + 1 = 1

    @pytest.mark.asyncio
    async def test_usage_without_cost_field(self):
        """usage 存在但无 cost 字段→credits_consumed=None（走兜底估算）"""
        lines = [
            _sse_line({
                "choices": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "model": "openai/gpt-4.1",
            }),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        assert chunks[-1].credits_consumed is None
        assert chunks[-1].prompt_tokens == 100

    @pytest.mark.asyncio
    async def test_usage_null_does_not_crash(self):
        """usage: null 不崩溃"""
        lines = [
            _sse_line({
                "choices": [{"delta": {"content": "ok"}, "index": 0, "finish_reason": None}],
                "usage": None,
                "model": "openai/gpt-4.1",
            }),
            "",
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content == "ok"
        assert chunks[0].prompt_tokens == 0
        assert chunks[0].credits_consumed is None

    @pytest.mark.asyncio
    async def test_done_signal_stops_iteration(self):
        """[DONE] 终止迭代"""
        lines = [
            _sse_line(_make_chunk(content="a")),
            "data: [DONE]",
            _sse_line(_make_chunk(content="should not appear")),
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1

    @pytest.mark.asyncio
    async def test_empty_lines_and_non_data_skipped(self):
        """空行和非 data: 行被跳过"""
        lines = [
            "", "event: ping", ": comment",
            _sse_line(_make_chunk(content="ok")),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self):
        """畸形 JSON 被跳过"""
        lines = [
            "data: {broken!!!",
            _sse_line(_make_chunk(content="ok")),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content == "ok"

    @pytest.mark.asyncio
    async def test_error_in_chunk_raises(self):
        """chunk 包含 error 时抛出 OpenRouterAPIError"""
        lines = [
            _sse_line({"error": {"message": "rate limited", "code": 429}}),
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        with pytest.raises(OpenRouterAPIError, match="rate limited"):
            async for _ in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        """HTTP 非 200 抛出 OpenRouterAPIError"""
        error_body = json.dumps({"error": {"message": "unauthorized"}}).encode()
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse([], status_code=401, error_body=error_body))

        with pytest.raises(OpenRouterAPIError, match="unauthorized"):
            async for _ in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_timeout_wrapped(self):
        """httpx.TimeoutException → OpenRouterAPIError"""
        adapter = _make_adapter()

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            raise httpx.TimeoutException("connect timeout")
            yield  # noqa

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        with pytest.raises(OpenRouterAPIError, match="Request timeout"):
            async for _ in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_finish_reason_propagated(self):
        """finish_reason 正确传播"""
        lines = [
            _sse_line(_make_chunk(content="done", finish_reason="stop")),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        _patch_stream(adapter, MockStreamResponse(lines))

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert chunks[0].finish_reason == "stop"


# ============================================================
# TestChatSync
# ============================================================


class TestChatSync:

    @pytest.mark.asyncio
    async def test_normal_response(self):
        adapter = _make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        adapter._client = AsyncMock()
        adapter._client.is_closed = False
        adapter._client.post = AsyncMock(return_value=mock_resp)

        result = await adapter.chat_sync(
            messages=[{"role": "user", "content": "hi"}]
        )

        assert result.content == "Hello!"
        assert result.finish_reason == "stop"
        assert result.prompt_tokens == 10

    @pytest.mark.asyncio
    async def test_usage_null_in_sync(self):
        adapter = _make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": None,
        }

        adapter._client = AsyncMock()
        adapter._client.is_closed = False
        adapter._client.post = AsyncMock(return_value=mock_resp)

        result = await adapter.chat_sync(
            messages=[{"role": "user", "content": "hi"}]
        )
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0

    @pytest.mark.asyncio
    async def test_http_error(self):
        adapter = _make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = json.dumps({"error": {"message": "server error"}}).encode()

        adapter._client = AsyncMock()
        adapter._client.is_closed = False
        adapter._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(OpenRouterAPIError, match="server error"):
            await adapter.chat_sync(messages=[{"role": "user", "content": "hi"}])


# ============================================================
# TestEstimateCost
# ============================================================


class TestEstimateCost:

    def test_known_model_pricing(self):
        """已知模型用 ModelConfig 价格估算"""
        adapter = _make_adapter("openai/gpt-4.1")
        result = adapter.estimate_cost_unified(
            input_tokens=1_000_000, output_tokens=1_000_000
        )
        # gpt-4.1: $2/1M input + $8/1M output = $10
        # credits = ceil(10 × 200) + 1 = 2001
        assert result.estimated_credits == math.ceil(10.0 * CREDITS_PER_USD) + CREDITS_MARKUP
        assert result.breakdown["input_tokens"] == 1_000_000
        assert result.breakdown["output_tokens"] == 1_000_000

    def test_unknown_model_returns_markup(self):
        """未知模型返回 CREDITS_MARKUP"""
        adapter = _make_adapter("unknown/model")
        result = adapter.estimate_cost_unified(input_tokens=100, output_tokens=100)
        assert result.estimated_credits == CREDITS_MARKUP

    def test_small_tokens(self):
        """少量 token 仍有最低 markup+1"""
        adapter = _make_adapter("openai/gpt-4.1")
        result = adapter.estimate_cost_unified(input_tokens=100, output_tokens=100)
        # $2 × 100/1M + $8 × 100/1M = $0.0002 + $0.0008 = $0.001
        # credits = ceil(0.001 × 200) + 1 = ceil(0.2) + 1 = 1 + 1 = 2
        assert result.estimated_credits >= CREDITS_MARKUP + 1


# ============================================================
# TestParseError
# ============================================================


class TestParseError:

    def test_json_error_body(self):
        body = json.dumps({"error": {"message": "bad request"}}).encode()
        result = OpenRouterChatAdapter._parse_error(body)
        assert result == "bad request"

    def test_non_json_body(self):
        body = b"x" * 1000
        result = OpenRouterChatAdapter._parse_error(body)
        assert len(result) == 500

    def test_json_without_error_key(self):
        body = json.dumps({"message": "fallback"}).encode()
        result = OpenRouterChatAdapter._parse_error(body)
        assert result == "fallback"


# ============================================================
# TestClose
# ============================================================


class TestClose:

    @pytest.mark.asyncio
    async def test_close_with_client(self):
        adapter = _make_adapter()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        adapter._client = mock_client

        await adapter.close()
        mock_client.aclose.assert_awaited_once()
        assert adapter._client is None

    @pytest.mark.asyncio
    async def test_close_without_client(self):
        adapter = _make_adapter()
        adapter._client = None
        await adapter.close()  # 不应抛异常
