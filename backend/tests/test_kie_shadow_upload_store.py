"""KIE 海外旁路临时空间链接缓存测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from services.adapters.kie.shadow_upload_store import (
    get_overseas_shadow_upload_staged_urls,
    save_overseas_shadow_upload_urls,
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
