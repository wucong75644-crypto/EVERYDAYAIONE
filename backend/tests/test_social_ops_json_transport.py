"""Exercise the HTTP boundary, not only the editorial gateway mock."""

import json
import httpx
import pytest
from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter


@pytest.mark.parametrize("model", ["deepseek-v4-pro", "deepseek-v4-flash"])
@pytest.mark.parametrize("stream", [True, False])
async def test_deepseek_json_contract_reaches_provider(model, stream):
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        if stream:
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
        )

    adapter = DashScopeChatAdapter("test", model)
    adapter._client = httpx.AsyncClient(
        base_url="https://model.invalid", transport=httpx.MockTransport(respond)
    )
    options = {
        "response_format": {"type": "json_object"},
        "thinking_mode": "disabled",
        "max_tokens": 4096,
    }
    try:
        if stream:
            chunks = [
                chunk
                async for chunk in adapter.stream_chat(
                    [{"role": "system", "content": "Return JSON"}], **options
                )
            ]
            assert "".join(chunk.content or "" for chunk in chunks) == "{}"
        else:
            assert (
                await adapter.chat_sync(
                    [{"role": "system", "content": "Return JSON"}], **options
                )
            ).content == "{}"
        assert sent[0]["response_format"] == {"type": "json_object"}
        assert sent[0]["enable_thinking"] is False
        assert sent[0]["max_tokens"] == 4096
        with pytest.raises(ValueError, match="DEEPSEEK_RESPONSE_FORMAT_UNSUPPORTED"):
            adapter._request_body(
                [],
                stream=stream,
                reasoning_effort=None,
                thinking_mode=None,
                response_format={"type": "json_schema"},
            )
        ordinary = adapter._request_body(
            [], stream=stream, reasoning_effort=None, thinking_mode="enabled"
        )
        assert "response_format" not in ordinary and ordinary["enable_thinking"] is True
    finally:
        await adapter.close()
