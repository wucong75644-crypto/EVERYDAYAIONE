"""版本化闭合历史缓存测试。"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.handlers.conversation_cache import (
    delete_messages,
    get_closed_messages,
    set_closed_messages,
)


def _redis_with_value(value):
    redis = AsyncMock()
    redis.get.return_value = value
    return redis


@pytest.mark.asyncio
async def test_exact_revision_and_boundary_returns_closed_history():
    redis = _redis_with_value(json.dumps({
        "schema_version": 6,
        "revision": 7,
        "through_message_id": "assistant-7",
        "closed_messages": [{"role": "user", "content": "历史"}],
    }))

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        result = await get_closed_messages(
            "conv-1", 7, "assistant-7", "org-1",
        )

    assert result == [{"role": "user", "content": "历史"}]
    redis.get.assert_awaited_once_with("conv:msgs:v6:org-1:conv-1")
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("revision", "through_message_id"),
    [(6, "assistant-7"), (7, "assistant-other")],
)
async def test_revision_or_boundary_mismatch_is_cache_miss(
    revision, through_message_id,
):
    redis = _redis_with_value(json.dumps({
        "schema_version": 6,
        "revision": 7,
        "through_message_id": "assistant-7",
        "closed_messages": [],
    }))

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        result = await get_closed_messages(
            "conv-1", revision, through_message_id, "org-1",
        )

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cached_value",
    [
        json.dumps([{"role": "user", "content": "旧数组"}]),
        json.dumps({"schema_version": 1, "messages": []}),
        json.dumps({
            "schema_version": 2,
            "revision": 1,
            "through_message_id": "assistant-1",
            "closed_messages": [{"role": "assistant", "content": "旧纯文本投影"}],
        }),
        json.dumps({
            "schema_version": 6,
            "revision": 1,
            "through_message_id": None,
            "closed_messages": [],
        }),
        json.dumps({
            "schema_version": 6,
            "revision": 1,
            "through_message_id": "assistant-1",
            "closed_messages": ["not-a-message"],
        }),
        "{not-json",
    ],
)
async def test_legacy_or_invalid_value_never_becomes_context(cached_value):
    redis = _redis_with_value(cached_value)

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        result = await get_closed_messages("conv-1", 1, "message-1", "org-1")

    assert result is None
    redis.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_uses_versioned_closed_history_envelope():
    redis = AsyncMock()

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        written = await set_closed_messages(
            "conv-1",
            3,
            "assistant-3",
            [{"role": "assistant", "content": "闭合回复"}],
            "org-1",
        )

    assert written is True
    key, ttl, raw = redis.setex.await_args.args
    assert key == "conv:msgs:v6:org-1:conv-1"
    assert ttl == 1800
    assert json.loads(raw) == {
        "schema_version": 6,
        "revision": 3,
        "through_message_id": "assistant-3",
        "closed_messages": [{"role": "assistant", "content": "闭合回复"}],
    }


@pytest.mark.asyncio
async def test_redis_failure_degrades_to_cache_miss():
    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(side_effect=RuntimeError("redis unavailable")),
    ):
        result = await get_closed_messages("conv-1", 2, "assistant-2", "org-1")

    assert result is None


@pytest.mark.asyncio
async def test_empty_identity_and_unavailable_redis_are_safe_misses():
    assert await get_closed_messages("", 0, None) is None
    assert await get_closed_messages("conv-1", -1, None) is None

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=None),
    ):
        assert await get_closed_messages("conv-1", 0, None) is None
        assert await set_closed_messages("conv-1", 0, None, []) is False


@pytest.mark.asyncio
async def test_bytes_payload_is_supported():
    redis = _redis_with_value(json.dumps({
        "schema_version": 6,
        "revision": 0,
        "through_message_id": None,
        "closed_messages": [],
    }).encode("utf-8"))

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        result = await get_closed_messages("conv-1", 0, None)

    assert result == []


@pytest.mark.asyncio
async def test_oversized_or_invalid_write_is_rejected():
    redis = AsyncMock()

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        assert await set_closed_messages("", 0, None, []) is False
        assert await set_closed_messages("conv-1", -1, None, []) is False
        written = await set_closed_messages(
            "conv-1",
            1,
            "assistant-1",
            [{"role": "user", "content": "x" * (260 * 1024)}],
        )

    assert written is False
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_and_delete_failures_do_not_escape():
    redis = AsyncMock()
    redis.setex.side_effect = RuntimeError("write failed")
    redis.delete.side_effect = RuntimeError("delete failed")

    with patch(
        "services.handlers.conversation_cache.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        written = await set_closed_messages("conv-1", 1, "assistant-1", [])
        await delete_messages("conv-1")
        await delete_messages("")

    assert written is False
    redis.delete.assert_awaited_once()
