"""Web Worker 与 Conversation Actor 的 Redis 交互响应测试。"""

import asyncio
from collections import defaultdict, deque
from unittest.mock import patch

import pytest

from services.websocket_manager import WebSocketManager


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def rpush(self, key, value):
        self.operations.append(("rpush", key, value))
        return self

    def expire(self, key, ttl):
        self.operations.append(("expire", key, ttl))
        return self

    async def execute(self):
        for operation, key, value in self.operations:
            if operation == "rpush":
                self.redis.queues[key].append(value)
        return [1] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.queues = defaultdict(deque)

    def pipeline(self):
        return FakePipeline(self)

    async def lpop(self, key):
        queue = self.queues[key]
        return queue.popleft() if queue else None


@pytest.mark.asyncio
async def test_confirm_crosses_process_boundary_with_tenant_scope():
    redis = FakeRedis()
    actor_manager = WebSocketManager()
    web_manager = WebSocketManager()

    with patch(
        "core.redis.RedisClient.get_client",
        return_value=redis,
    ):
        waiting = asyncio.create_task(actor_manager.wait_for_confirm(
            "tc-1", "user-1", "org-a", timeout=1.0,
        ))
        await asyncio.sleep(0)
        assert await web_manager.resolve_confirm(
            "tc-1", "user-1", "org-a", True,
        ) is True
        assert await waiting is True


@pytest.mark.asyncio
async def test_steer_crosses_process_boundary_without_leaking_org():
    redis = FakeRedis()
    actor_manager = WebSocketManager()
    web_manager = WebSocketManager()

    with patch(
        "core.redis.RedisClient.get_client",
        return_value=redis,
    ):
        actor_manager.register_steer_listener("task-1", "org-a")
        assert web_manager.resolve_steer(
            "task-1", "继续", org_id="org-b",
        ) is True
        await asyncio.sleep(0.15)
        assert actor_manager.check_steer("task-1", "org-a") is None

        assert web_manager.resolve_steer(
            "task-1", "继续", org_id="org-a",
        ) is True
        await asyncio.sleep(0.15)
        assert actor_manager.check_steer("task-1", "org-a") == "继续"
