"""Context Runtime 摘要模型边界测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.agent.runtime.context.summary_model import call_summary_model


@pytest.mark.asyncio
async def test_call_summary_model_uses_runtime_prompt_and_bound() -> None:
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": "x" * 20}}],
    }
    client = AsyncMock()
    client.post.return_value = response

    with patch(
        "services.agent.runtime.context.summary_model._client.get",
        new=AsyncMock(return_value=client),
    ):
        result = await call_summary_model(
            "model-1",
            "source",
            system_prompt="runtime prompt",
            max_chars=10,
        )

    assert result == "x" * 10
    payload = client.post.await_args.kwargs["json"]
    assert payload["messages"][0]["content"] == "runtime prompt"
    assert payload["max_tokens"] == 20


@pytest.mark.asyncio
async def test_call_summary_model_timeout_returns_none() -> None:
    client = AsyncMock()
    client.post.side_effect = httpx.TimeoutException("timeout")

    with patch(
        "services.agent.runtime.context.summary_model._client.get",
        new=AsyncMock(return_value=client),
    ):
        result = await call_summary_model("model-1", "source")

    assert result is None
