"""Real isolated Redis Lua contention; never uses project Redis settings."""
import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from core.exceptions import TaskQueueFullError
from services.task_limit_service import TaskLimitService


@pytest.mark.asyncio
async def test_real_image_slots_are_atomic_and_independent():
    socket=os.getenv("CHAT_IMAGE_TEST_REDIS_SOCKET")
    if not socket:
        pytest.skip("Explicit isolated Redis socket required")
    assert socket == "/private/tmp/everydayai-chat-image-redis.sock"
    redis=Redis(unix_socket_path=socket,decode_responses=True)
    user="isolated-"+uuid4().hex
    conv="conversation"
    service=TaskLimitService(redis)
    service.global_limit=4
    service.conversation_limit=3
    global_key=service._global_key(user)
    conv_key=service._conversation_key(user,conv)
    try:
        await redis.sadd(global_key,"parent-chat")
        await redis.sadd(conv_key,"parent-chat")
        async def attempt(index):
            try:
                return await service.acquire_image_slot(user,conv,f"child-{index}")
            except TaskQueueFullError:
                return None
        slots=await asyncio.gather(*(attempt(i) for i in range(20)))
        accepted=[slot for slot in slots if slot]
        assert len(accepted)==2
        assert await redis.scard(global_key)==3
        assert await service.acquire_image_slot(user,conv,accepted[0])==accepted[0]
        assert await redis.scard(conv_key)==3
        await service.release(user,conv,slot_id=accepted[0])
        assert await redis.sismember(conv_key,"parent-chat")
        assert await redis.scard(conv_key)==2
        assert not await redis.sismember(conv_key,accepted[0])
        # Reconstitute an independent child whose conversation set entry was lost.
        service.global_limit=2
        await redis.srem(conv_key,accepted[1])
        assert await service.acquire_image_slot(user,conv,accepted[1])==accepted[1]
        assert await redis.scard(global_key)==2
        assert await redis.scard(conv_key)==2
    finally:
        await redis.delete(global_key,conv_key)
        await redis.aclose()


@pytest.mark.asyncio
async def test_image_slot_redis_failure_never_degrades_to_permission():
    from unittest.mock import AsyncMock
    redis=AsyncMock()
    redis.eval.side_effect=ConnectionError("isolated failure")
    with pytest.raises(ConnectionError):
        await TaskLimitService(redis).acquire_image_slot("user","conv","child")
