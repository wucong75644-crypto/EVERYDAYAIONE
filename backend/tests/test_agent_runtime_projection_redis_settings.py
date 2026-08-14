"""Explicit Redis boundary used only by the Projection process."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent_runtime_worker_main import (
    ProjectionProcessSettings,
    _configure_projection_redis,
)
from core.redis import RedisClient
from services.websocket_manager import WebSocketManager


@pytest.fixture(autouse=True)
def reset_redis_client_state():
    original_instance = RedisClient._instance
    original_configuration = RedisClient._explicit_configuration
    RedisClient._instance = None
    RedisClient._explicit_configuration = None
    yield
    RedisClient._instance = original_instance
    RedisClient._explicit_configuration = original_configuration


def _settings() -> ProjectionProcessSettings:
    return ProjectionProcessSettings(
        worker_database_url="postgresql://projection@127.0.0.1/runtime",
        agent_runtime_process_role="projection",
        agent_runtime_worker_id="projection-test",
        agent_runtime_release_revision="test-revision",
        agent_runtime_health_socket="/tmp/projection-test.sock",
        redis_host="redis.internal",
        redis_port=6380,
        redis_password="projection-secret",
        redis_db=3,
        redis_ssl=True,
        agent_runtime_media_enabled=False,
        media_cdn_domain="cdn.example.test",
    )


@pytest.mark.asyncio
async def test_projection_redis_is_explicit_and_shared_by_publish_path() -> None:
    settings = _settings()
    client = AsyncMock()
    _configure_projection_redis(settings)

    with patch("core.redis.Redis", return_value=client) as constructor:
        assert await RedisClient.get_client() is client
        manager = WebSocketManager()
        await manager._publish(
            "user", "user-1", {"type": "confirmation"}, org_id="org-1",
        )

    constructor.assert_called_once_with(
        host="redis.internal", port=6380, password="projection-secret", db=3,
        ssl=True, encoding="utf-8", decode_responses=True,
        socket_timeout=5.0, socket_connect_timeout=5.0,
    )
    client.publish.assert_awaited_once()
    assert "projection-secret" not in repr(settings)


def test_projection_redis_rejects_conflicting_configuration() -> None:
    _configure_projection_redis(_settings())
    with pytest.raises(RuntimeError, match="REDIS_CONFIGURATION_CONFLICT"):
        RedisClient.configure_explicit(
            host="other.internal", port=6379, password=None, db=0, ssl=False,
        )


def test_projection_build_path_does_not_start_websocket_listener() -> None:
    from pathlib import Path

    entrypoint = Path(__file__).parents[1] / "agent_runtime_worker_main.py"
    text = entrypoint.read_text()
    assert "start_redis_listener" not in text
