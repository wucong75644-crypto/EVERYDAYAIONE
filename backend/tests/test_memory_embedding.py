"""记忆批量 embedding 适配测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.memory.embedding import get_embedding, get_embeddings


@pytest.mark.asyncio
async def test_empty_embedding_batch_skips_provider():
    assert await get_embeddings([]) == []


@pytest.mark.asyncio
async def test_batch_embedding_preserves_input_order():
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=SimpleNamespace(data=[
        SimpleNamespace(embedding=[1.0, 0.0]),
        SimpleNamespace(embedding=[0.0, 1.0]),
    ]))

    with patch("openai.AsyncOpenAI", return_value=client):
        result = await get_embeddings(["第一条", "第二条"])

    assert result == [[1.0, 0.0], [0.0, 1.0]]
    assert client.embeddings.create.await_args.kwargs["input"] == [
        "第一条",
        "第二条",
    ]


@pytest.mark.asyncio
async def test_incomplete_embedding_batch_fails_closed():
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=SimpleNamespace(data=[]))

    with patch("openai.AsyncOpenAI", return_value=client):
        assert await get_embedding("记忆") is None


@pytest.mark.asyncio
async def test_non_finite_embedding_fails_closed():
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=SimpleNamespace(data=[
        SimpleNamespace(embedding=[float("nan")]),
    ]))

    with patch("openai.AsyncOpenAI", return_value=client):
        assert await get_embedding("记忆") is None
