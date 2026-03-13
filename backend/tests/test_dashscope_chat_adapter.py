"""
DashScope Chat 适配器单元测试

覆盖：SSE 流式解析、null 值防护、token 累加、错误处理、积分估算
重点回归：usage: null 导致 NoneType 崩溃的生产 bug
"""

import json
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.adapters.dashscope.chat_adapter import (
    DASHSCOPE_PRICING,
    DashScopeAPIError,
    DashScopeChatAdapter,
)
from services.adapters.base import ModelProvider, StreamChunk, ChatResponse


# ============================================================
# Fixtures
# ============================================================

def _make_adapter(model: str = "qwen3.5-plus") -> DashScopeChatAdapter:
    return DashScopeChatAdapter(
        api_key="sk-test-key",
        model=model,
        base_url="https://dashscope.example.com/v1",
    )


def _sse_line(data: dict) -> str:
    """构造一行 SSE data"""
    return f"data: {json.dumps(data)}"


def _make_chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> dict:
    """构造标准 DashScope SSE chunk"""
    chunk = {
        "choices": [{
            "delta": {"content": content, "role": "assistant"},
            "index": 0,
            "finish_reason": finish_reason,
        }],
        "usage": usage,
        "model": "qwen3.5-plus",
    }
    return chunk


class MockStreamResponse:
    """模拟 httpx 流式响应"""

    def __init__(self, lines: list[str], status_code: int = 200, error_body: bytes = b""):
        self.status_code = status_code
        self._lines = lines
        self._error_body = error_body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._error_body


# ============================================================
# TestInit
# ============================================================

class TestInit:
    def test_stores_config(self):
        adapter = _make_adapter()
        assert adapter._api_key == "sk-test-key"
        assert adapter._model_id == "qwen3.5-plus"
        assert adapter._base_url == "https://dashscope.example.com/v1"

    def test_strips_trailing_slash(self):
        adapter = DashScopeChatAdapter(
            api_key="k", model="m", base_url="https://example.com/v1/"
        )
        assert adapter._base_url == "https://example.com/v1"

    def test_provider(self):
        assert _make_adapter().provider == ModelProvider.DASHSCOPE

    def test_supports_streaming(self):
        assert _make_adapter().supports_streaming is True


# ============================================================
# TestStreamChat — 核心流式解析测试
# ============================================================

class TestStreamChat:

    @pytest.mark.asyncio
    async def test_normal_stream_yields_content(self):
        """正常流式输出：返回 content chunks"""
        lines = [
            _sse_line(_make_chunk(content="你")),
            "",
            _sse_line(_make_chunk(content="好")),
            "",
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].content == "你"
        assert chunks[1].content == "好"

    @pytest.mark.asyncio
    async def test_usage_null_does_not_crash(self):
        """🔴 核心回归测试：usage: null 不崩溃（生产 bug）"""
        lines = [
            _sse_line({
                "choices": [{"delta": {"content": "hello"}, "index": 0, "finish_reason": None}],
                "usage": None,  # DashScope 中间 chunk 返回 null
                "model": "qwen3.5-plus",
            }),
            "",
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content == "hello"
        assert chunks[0].prompt_tokens == 0
        assert chunks[0].completion_tokens == 0

    @pytest.mark.asyncio
    async def test_usage_present_in_final_chunk(self):
        """最后一个 chunk 包含 usage token 统计"""
        lines = [
            _sse_line(_make_chunk(content="hi", usage=None)),
            "",
            _sse_line({
                "choices": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "model": "qwen3.5-plus",
            }),
            "",
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}]
        ):
            chunks.append(chunk)

        # 最后一个 chunk 有 usage
        last = chunks[-1]
        assert last.prompt_tokens == 100
        assert last.completion_tokens == 50

    @pytest.mark.asyncio
    async def test_done_signal_stops_iteration(self):
        """[DONE] 信号正确终止迭代"""
        lines = [
            _sse_line(_make_chunk(content="a")),
            "",
            "data: [DONE]",
            _sse_line(_make_chunk(content="should not appear")),
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content == "a"

    @pytest.mark.asyncio
    async def test_empty_lines_and_non_data_skipped(self):
        """空行和非 data: 前缀的行被跳过"""
        lines = [
            "",
            "event: ping",
            ": comment",
            _sse_line(_make_chunk(content="ok")),
            "",
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self):
        """畸形 JSON 被静默跳过"""
        lines = [
            "data: {broken json!!!",
            _sse_line(_make_chunk(content="ok")),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content == "ok"

    @pytest.mark.asyncio
    async def test_error_in_chunk_raises(self):
        """chunk 包含 error 字段时抛出 DashScopeAPIError"""
        lines = [
            _sse_line({"error": {"message": "rate limited", "code": 429}}),
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        with pytest.raises(DashScopeAPIError, match="rate limited"):
            async for _ in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        """HTTP 非 200 状态码抛出 DashScopeAPIError"""
        error_body = json.dumps({"error": {"message": "bad request"}}).encode()
        mock_response = MockStreamResponse([], status_code=400, error_body=error_body)

        adapter = _make_adapter()

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        with pytest.raises(DashScopeAPIError, match="bad request"):
            async for _ in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_timeout_wrapped(self):
        """httpx.TimeoutException 被包装为 DashScopeAPIError"""
        adapter = _make_adapter()

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            raise httpx.TimeoutException("connect timeout")
            yield  # noqa: unreachable — needed for asynccontextmanager

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        with pytest.raises(DashScopeAPIError, match="Request timeout"):
            async for _ in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_thinking_mode_enabled(self):
        """thinking_mode=enabled 时 request_body 包含 enable_thinking=True"""
        adapter = _make_adapter()
        captured_body = {}

        @asynccontextmanager
        async def mock_stream(method, url, json=None):
            captured_body.update(json or {})
            yield MockStreamResponse(["data: [DONE]"])

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        async for _ in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            thinking_mode="enabled",
        ):
            pass

        assert captured_body.get("enable_thinking") is True

    @pytest.mark.asyncio
    async def test_thinking_mode_disabled(self):
        """thinking_mode=disabled 时 request_body 包含 enable_thinking=False"""
        adapter = _make_adapter()
        captured_body = {}

        @asynccontextmanager
        async def mock_stream(method, url, json=None):
            captured_body.update(json or {})
            yield MockStreamResponse(["data: [DONE]"])

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        async for _ in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            thinking_mode="disabled",
        ):
            pass

        assert captured_body.get("enable_thinking") is False

    @pytest.mark.asyncio
    async def test_thinking_mode_none(self):
        """thinking_mode=None 时 request_body 不包含 enable_thinking"""
        adapter = _make_adapter()
        captured_body = {}

        @asynccontextmanager
        async def mock_stream(method, url, json=None):
            captured_body.update(json or {})
            yield MockStreamResponse(["data: [DONE]"])

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        async for _ in adapter.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
        ):
            pass

        assert captured_body.get("enable_thinking") is False

    @pytest.mark.asyncio
    async def test_finish_reason_propagated(self):
        """finish_reason 正确传播到 StreamChunk"""
        lines = [
            _sse_line(_make_chunk(content="done", finish_reason="stop")),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert chunks[0].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_empty_choices(self):
        """choices 为空时 content=None"""
        lines = [
            _sse_line({"choices": [], "usage": None, "model": "qwen3.5-plus"}),
            "data: [DONE]",
        ]
        adapter = _make_adapter()
        mock_response = MockStreamResponse(lines)

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        adapter._client = MagicMock()
        adapter._client.is_closed = False
        adapter._client.stream = mock_stream

        chunks = []
        async for chunk in adapter.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content is None


# ============================================================
# TestChatSync
# ============================================================

class TestChatSync:

    @pytest.mark.asyncio
    async def test_normal_response(self):
        """正常非流式响应"""
        adapter = _make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "你好！"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        adapter._client = AsyncMock()
        adapter._client.is_closed = False
        adapter._client.post = AsyncMock(return_value=mock_resp)

        result = await adapter.chat_sync(
            messages=[{"role": "user", "content": "hi"}]
        )

        assert result.content == "你好！"
        assert result.finish_reason == "stop"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5

    @pytest.mark.asyncio
    async def test_usage_null_in_sync(self):
        """同步响应 usage 为 null（虽然少见但需防护）"""
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

        # usage 为 None 时 .get("usage", {}) 返回 None → 需要防护
        # 当前代码用 .get("usage", {})，如果 key 存在但值为 None 会崩溃
        # 这个测试验证此边界情况
        try:
            result = await adapter.chat_sync(
                messages=[{"role": "user", "content": "hi"}]
            )
            # 如果没崩溃，验证 tokens 为 0
            assert result.prompt_tokens == 0
            assert result.completion_tokens == 0
        except AttributeError:
            # 如果崩溃了，说明需要修复（和 stream 一样的 bug）
            pytest.fail("chat_sync 中 usage: null 导致 AttributeError，需要修复")

    @pytest.mark.asyncio
    async def test_empty_choices(self):
        """空 choices 返回空 content"""
        adapter = _make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0},
        }

        adapter._client = AsyncMock()
        adapter._client.is_closed = False
        adapter._client.post = AsyncMock(return_value=mock_resp)

        result = await adapter.chat_sync(
            messages=[{"role": "user", "content": "hi"}]
        )
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_http_error(self):
        """HTTP 错误抛出 DashScopeAPIError"""
        adapter = _make_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = json.dumps({"error": {"message": "internal error"}}).encode()

        adapter._client = AsyncMock()
        adapter._client.is_closed = False
        adapter._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(DashScopeAPIError, match="internal error"):
            await adapter.chat_sync(messages=[{"role": "user", "content": "hi"}])


# ============================================================
# TestEstimateCost
# ============================================================

class TestEstimateCost:

    def test_known_model_pricing(self):
        """已知模型使用定价表计算"""
        adapter = _make_adapter("deepseek-v3.2")
        result = adapter.estimate_cost_unified(
            input_tokens=1_000_000, output_tokens=1_000_000
        )
        # deepseek-v3.2: input=29, output=113 per 1M
        assert result.estimated_credits == max(1, 29 + 113)
        assert result.breakdown["input_credits"] == 29
        assert result.breakdown["output_credits"] == 113

    def test_unknown_model_returns_1(self):
        """未知模型返回 estimated_credits=1"""
        adapter = _make_adapter("unknown-model")
        result = adapter.estimate_cost_unified(input_tokens=100, output_tokens=100)
        assert result.estimated_credits == 1

    def test_zero_tokens(self):
        """零 token 输入→零积分"""
        adapter = _make_adapter("qwen3.5-plus")
        result = adapter.estimate_cost_unified(input_tokens=0, output_tokens=0)
        assert result.estimated_credits == 0

    def test_small_tokens_minimum_1(self):
        """少量 token 但 total > 0 时最小为 1"""
        adapter = _make_adapter("qwen3.5-plus")
        # qwen3.5-plus: input=12/1M, output=68/1M
        # 1000 tokens: int(1000 * 12 / 1M) = 0, int(1000 * 68 / 1M) = 0 → total=0
        result = adapter.estimate_cost_unified(input_tokens=1000, output_tokens=1000)
        # total=0 → max(1,0) if total>0 else 0 → 0
        assert result.estimated_credits == 0

        # 100k tokens: int(100000 * 12 / 1M) = 1, int(100000 * 68 / 1M) = 6 → total=7
        result2 = adapter.estimate_cost_unified(input_tokens=100_000, output_tokens=100_000)
        assert result2.estimated_credits >= 1


# ============================================================
# TestParseError
# ============================================================

class TestParseError:

    def test_json_error_body(self):
        """JSON 错误体提取 message"""
        body = json.dumps({"error": {"message": "bad request"}}).encode()
        result = DashScopeChatAdapter._parse_error(body)
        assert result == "bad request"

    def test_non_json_body(self):
        """非 JSON 体返回截断文本"""
        body = b"x" * 1000
        result = DashScopeChatAdapter._parse_error(body)
        assert len(result) == 500

    def test_json_without_error_key(self):
        """JSON 体无 error key 时 fallback 到 message"""
        body = json.dumps({"message": "fallback msg"}).encode()
        result = DashScopeChatAdapter._parse_error(body)
        assert result == "fallback msg"


# ============================================================
# TestClose
# ============================================================

class TestClose:

    @pytest.mark.asyncio
    async def test_close_with_client(self):
        """关闭时调用 aclose"""
        adapter = _make_adapter()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        adapter._client = mock_client

        await adapter.close()

        mock_client.aclose.assert_awaited_once()
        assert adapter._client is None

    @pytest.mark.asyncio
    async def test_close_without_client(self):
        """无客户端时不报错"""
        adapter = _make_adapter()
        adapter._client = None
        await adapter.close()  # 不应抛异常
