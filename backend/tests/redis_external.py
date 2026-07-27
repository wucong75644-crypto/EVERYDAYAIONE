"""Opt-in fixtures for isolated localhost Redis contract tests."""

from __future__ import annotations

from dataclasses import dataclass
import os
import secrets
from urllib.parse import urlparse

import pytest
from redis.asyncio import Redis


_RUN_FLAG = "RUN_REDIS_EXTERNAL_TESTS"
_TEST_URL = "REDIS_TEST_URL"
_KEY_ROOT = "everydayai:test:redis-contract"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True)
class RedisExternalHarness:
    client: Redis
    url: str
    namespace: str

    def key(self, suffix: str) -> str:
        if not suffix or ":" in suffix:
            raise ValueError("Redis test key suffix must be one non-empty segment")
        return f"{self.namespace}{suffix}"

    def new_client(self) -> Redis:
        return Redis.from_url(
            self.url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )


def _validated_test_url() -> str:
    if os.getenv(_RUN_FLAG) != "1":
        pytest.skip(f"{_RUN_FLAG}=1 is required")
    url = os.getenv(_TEST_URL, "")
    if not url:
        pytest.fail(f"{_TEST_URL} is required", pytrace=False)
    parsed = urlparse(url)
    if parsed.scheme not in {"redis", "rediss"}:
        pytest.fail("REDIS_TEST_URL must use redis or rediss", pytrace=False)
    if parsed.hostname not in _LOCAL_HOSTS:
        pytest.fail("REDIS_TEST_URL must target localhost", pytrace=False)
    if parsed.port is None:
        pytest.fail("REDIS_TEST_URL must use an explicit port", pytrace=False)
    configured_url = os.getenv("REDIS_URL")
    if configured_url and _normalized_url(configured_url) == _normalized_url(url):
        pytest.fail("REDIS_TEST_URL must not equal REDIS_URL", pytrace=False)
    return url


def _normalized_url(url: str) -> tuple[str | None, int | None, str]:
    parsed = urlparse(url)
    return (
        parsed.hostname,
        parsed.port,
        parsed.path or "/0",
    )


async def _keys(client: Redis, namespace: str) -> list[str]:
    return [
        key
        async for key in client.scan_iter(match=f"{namespace}*", count=100)
    ]


@pytest.fixture
async def redis_external() -> RedisExternalHarness:
    url = _validated_test_url()
    namespace = f"{_KEY_ROOT}:{secrets.token_hex(16)}:"
    client = Redis.from_url(
        url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
    try:
        assert await client.ping() is True
        assert await _keys(client, namespace) == []
        yield RedisExternalHarness(
            client=client,
            url=url,
            namespace=namespace,
        )
    finally:
        residual = await _keys(client, namespace)
        if residual:
            await client.delete(*residual)
        assert await _keys(client, namespace) == []
        await client.aclose()
