"""AR-09 所需的现有 Provider 响应元数据回归。"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.adapters.dashscope.chat_adapter import (
    _parse_stream_chunk as parse_dashscope_chunk,
)
from services.adapters.google.chat_adapter import GoogleChatAdapter
from services.adapters.kie.unified_chat import KieUnifiedChatMixin
from services.adapters.openrouter.chat_adapter import (
    _parse_stream_chunk as parse_openrouter_chunk,
)
from services.agent.runtime.infrastructure.model.projection import (
    provider_kwargs,
)
from services.agent.runtime.ports import ModelRequestOptions


def _provider_chunk() -> dict:
    return {
        "choices": [{
            "delta": {
                "content": None,
                "reasoning_content": "thinking",
                "refusal": "declined",
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "completion_tokens_details": {"reasoning_tokens": 20},
            "prompt_tokens_details": {"cached_tokens": 60},
            "cache_creation_input_tokens": 10,
        },
    }


def test_dashscope_preserves_modelport_usage_and_refusal() -> None:
    chunk = parse_dashscope_chunk(
        _provider_chunk(),
        tools_enabled=False,
        model_id="qwen3.5-plus",
    )

    assert chunk.finish_reason == "stop"
    assert chunk.refusal is True
    assert chunk.reasoning_tokens == 20
    assert chunk.cached_tokens == 60
    assert chunk.cache_creation_tokens == 10


def test_openrouter_preserves_modelport_usage_and_refusal() -> None:
    raw = _provider_chunk()
    raw["usage"]["cost"] = 0.01
    raw["choices"][0]["delta"]["tool_calls"] = [{
        "index": 0,
        "id": "call-1",
        "function": {"name": "lookup", "arguments": '{"id":1}'},
    }]

    chunk = parse_openrouter_chunk(raw)

    assert chunk.finish_reason == "stop"
    assert chunk.refusal is True
    assert chunk.reasoning_tokens == 20
    assert chunk.cached_tokens == 60
    assert chunk.cache_creation_tokens == 10
    assert chunk.credits_consumed is not None
    assert chunk.tool_calls and chunk.tool_calls[0].id == "call-1"


@pytest.mark.asyncio
async def test_google_format_refactor_preserves_text_and_media() -> None:
    adapter = object.__new__(GoogleChatAdapter)
    adapter._download_media = AsyncMock(return_value="base64-data")

    result = await adapter._convert_to_google_format([
        {"role": "system", "content": "rules"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "answer"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/a.png"},
                },
            ],
        },
    ])

    assert result == [
        {"role": "user", "parts": [{"text": "rules"}]},
        {
            "role": "model",
            "parts": [
                {"text": "answer"},
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": "base64-data",
                    }
                },
            ],
        },
    ]


def test_structured_request_is_part_of_provider_projection() -> None:
    options = ModelRequestOptions(
        structured_output=True,
        response_schema_revision="schema-r1",
    )

    assert provider_kwargs(options)["response_format"] == {
        "type": "json_object"
    }


@pytest.mark.asyncio
async def test_kie_preserves_reasoning_usage() -> None:
    delta = SimpleNamespace(
        content="answer",
        reasoning_content=None,
        tool_calls=None,
    )
    usage = SimpleNamespace(
        prompt_tokens=9,
        completion_tokens=6,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
    )
    provider_chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason="stop")],
        usage=usage,
        credits_consumed=None,
    )

    class DummyKie(KieUnifiedChatMixin):
        def format_messages_from_history(self, messages: list[dict]):
            return messages

        async def chat(self, **_kwargs: Any):
            async def stream():
                yield provider_chunk

            return stream()

    chunks = [
        chunk
        async for chunk in DummyKie().stream_chat(
            [{"role": "user", "content": "hi"}]
        )
    ]

    assert chunks[0].reasoning_tokens == 4
    assert chunks[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_google_preserves_finish_reasoning_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function_call = SimpleNamespace(
        id="call-1",
        name="lookup",
        args={"id": 1},
    )
    usage = SimpleNamespace(
        prompt_token_count=8,
        candidates_token_count=5,
        thoughts_token_count=3,
        cached_content_token_count=2,
    )
    provider_chunk = SimpleNamespace(
        text="answer",
        usage_metadata=usage,
        candidates=[SimpleNamespace(
            finish_reason="STOP",
            content=SimpleNamespace(parts=[
                SimpleNamespace(function_call=function_call),
            ]),
        )],
    )
    captured: dict[str, Any] = {}

    class Client:
        async def generate_content_stream(self, **kwargs: Any):
            captured.update(kwargs)
            yield provider_chunk

    adapter = object.__new__(GoogleChatAdapter)
    adapter._model_id = "gemini-test"
    adapter.client = Client()

    async def convert(
        _self: GoogleChatAdapter,
        messages: list[dict],
    ) -> list[dict]:
        return messages

    monkeypatch.setattr(GoogleChatAdapter, "_convert_to_google_format", convert)
    tools = [{
        "type": "function",
        "function": {
            "name": "lookup",
            "parameters": {"type": "object"},
        },
    }]
    chunks = [
        chunk
        async for chunk in adapter.stream_chat(
            [{"role": "user", "content": "hi"}],
            tools=tools,
        )
    ]

    assert captured["config"]["tools"] == [{
        "function_declarations": [tools[0]["function"]],
    }]
    assert chunks[0].finish_reason == "STOP"
    assert chunks[0].reasoning_tokens == 3
    assert chunks[0].tool_calls
    assert chunks[0].tool_calls[0].arguments_delta == '{"id":1}'
