"""Redis WebSocket 投递的租户边界测试。"""

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from services.websocket_manager import WebSocketManager


@dataclass
class FakeConnection:
    org_id: str | None


@pytest.fixture
def manager() -> WebSocketManager:
    instance = WebSocketManager()
    instance.send_to_connection = AsyncMock(return_value=True)
    return instance


@pytest.mark.asyncio
async def test_redis_user_delivery_treats_none_as_personal(manager):
    manager._connections["user-1"] = {
        "personal": FakeConnection(None),
        "enterprise": FakeConnection("org-a"),
    }

    await manager._deliver_from_redis({
        "target_type": "user",
        "target_id": "user-1",
        "org_id": None,
        "message": {"type": "test"},
    })

    manager.send_to_connection.assert_called_once_with(
        "personal", {"type": "test"},
    )


@pytest.mark.asyncio
async def test_redis_task_delivery_uses_composite_scope(manager):
    manager._task_subscribers = {
        ("task-1", "org-a"): {"conn-a"},
        ("task-1", "org-b"): {"conn-b"},
    }

    await manager._deliver_from_redis({
        "target_type": "task",
        "target_id": "task-1",
        "org_id": "org-b",
        "message": {"type": "test"},
    })

    manager.send_to_connection.assert_called_once_with(
        "conn-b", {"type": "test"},
    )


@pytest.mark.asyncio
async def test_publish_keeps_explicit_personal_org_scope(manager):
    redis_client = AsyncMock()
    with patch(
        "core.redis.RedisClient.get_client",
        new=AsyncMock(return_value=redis_client),
    ):
        await manager._publish(
            "user", "user-1", {"type": "test"}, org_id=None,
        )

    payload = json.loads(redis_client.publish.call_args.args[1])
    assert "org_id" in payload
    assert payload["org_id"] is None
