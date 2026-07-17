"""WebSocket Redis 发布与订阅生命周期测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.websocket_manager import WebSocketManager
from services.websocket_redis import WS_CHANNEL


@pytest.mark.asyncio
async def test_publish_works_without_local_listener() -> None:
    """无头企微/Actor 进程未启动 listener 时仍可发布。"""
    manager = WebSocketManager()
    client = AsyncMock()

    with patch(
        "core.redis.RedisClient.get_client",
        new=AsyncMock(return_value=client),
    ):
        await manager._publish(
            "user",
            "user-1",
            {"type": "conversation_updated"},
            org_id="org-1",
        )

    client.publish.assert_awaited_once()
    channel, raw_payload = client.publish.await_args.args
    payload = json.loads(raw_payload)
    assert channel == WS_CHANNEL
    assert payload["target_id"] == "user-1"
    assert payload["org_id"] == "org-1"


@pytest.mark.asyncio
async def test_publish_failure_is_best_effort() -> None:
    """Redis 暂时不可用不应破坏数据库事实链路。"""
    manager = WebSocketManager()

    with patch(
        "core.redis.RedisClient.get_client",
        new=AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        await manager._publish(
            "user", "user-1", {"type": "message_done"},
        )


@pytest.mark.asyncio
async def test_remote_user_delivery_filters_by_org() -> None:
    """其他 Web worker 接收事件时也必须执行组织隔离。"""
    manager = WebSocketManager()
    manager.send_to_connection = AsyncMock(return_value=True)
    manager._connections["user-1"] = {
        "conn-a": SimpleNamespace(org_id="org-a"),
        "conn-b": SimpleNamespace(org_id="org-b"),
    }

    await manager._deliver_from_redis({
        "target_type": "user",
        "target_id": "user-1",
        "org_id": "org-a",
        "message": {"type": "message_done"},
    })

    manager.send_to_connection.assert_awaited_once_with(
        "conn-a", {"type": "message_done"},
    )
