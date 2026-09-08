"""KIE 海外旁路临时空间链接缓存测试。"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.adapters.kie.shadow_upload_store import (
    get_overseas_shadow_upload_staged_urls,
    save_overseas_shadow_upload_urls,
    get_overseas_shadow_upload,
    save_overseas_shadow_upload_status,
)


class _FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, ex):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)


@pytest.mark.asyncio
async def test_cached_staged_urls_preserve_kie_input_order():
    redis = _FakeRedis()
    source_urls = [
        "https://cdn.example.com/input-1.png",
        "https://cdn.example.com/input-2.png",
    ]
    staged_urls = [
        "https://tempfile.redpandaai.co/staged-1.png",
        "https://tempfile.redpandaai.co/staged-2.png",
    ]

    with patch(
        "services.adapters.kie.shadow_upload_store.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        saved = await save_overseas_shadow_upload_urls(
            "task-123", source_urls, staged_urls
        )
        cached_urls = await get_overseas_shadow_upload_staged_urls("task-123")

    assert saved is True
    assert cached_urls == staged_urls


async def test_upload_phases_and_request_snapshot():
    redis = _FakeRedis()
    snapshot = {"model": "gpt-image-2-image-to-image", "input": {"resolution": "1K"}}
    with patch("services.adapters.kie.shadow_upload_store.get_redis", AsyncMock(return_value=redis)):
        assert await save_overseas_shadow_upload_status("t", "pending", ["https://cdn.test/a"], snapshot)
        assert (await get_overseas_shadow_upload("t"))["status"] == "pending"
        assert await get_overseas_shadow_upload_staged_urls("t") is None
        assert await save_overseas_shadow_upload_urls("t", ["https://cdn.test/a"], ["https://kie.test/a"], snapshot)
        ready = await get_overseas_shadow_upload("t")
        assert ready["status"] == "ready" and ready["request"] == snapshot
        assert ready["updated_at"] > 0
        assert await save_overseas_shadow_upload_status("failed", "failed", ["https://cdn.test/a"])
        assert (await get_overseas_shadow_upload("failed"))["status"] == "failed"


@pytest.mark.parametrize("payload", ["{invalid", "[]", '{"source_urls":["a"],"staged_urls":[]}', '{"status":"surprise"}', '{"status":[]}', '{"updated_at":"bad"}'])
async def test_malformed_cache_is_explicit_failure(payload):
    redis = _FakeRedis()
    redis.data["kie:shadow-overseas-upload:t"] = payload
    with patch("services.adapters.kie.shadow_upload_store.get_redis", AsyncMock(return_value=redis)):
        assert (await get_overseas_shadow_upload("t"))["status"] == "failed"


async def test_old_cache_compatibility():
    redis = _FakeRedis()
    redis.data["kie:shadow-overseas-upload:t"] = json.dumps({"source_urls": ["a"], "staged_urls": ["b"]})
    with patch("services.adapters.kie.shadow_upload_store.get_redis", AsyncMock(return_value=redis)):
        assert (await get_overseas_shadow_upload("t"))["status"] == "ready"


async def test_redis_unavailable_is_safe():
    with patch("services.adapters.kie.shadow_upload_store.get_redis", AsyncMock(side_effect=RuntimeError("offline"))):
        assert await get_overseas_shadow_upload("t") is None
        assert await save_overseas_shadow_upload_status("t", "pending", ["a"]) is False
