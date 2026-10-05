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


@pytest.mark.asyncio
async def test_shared_fifteen_slots_for_chat_and_images_across_conversations():
    socket = os.getenv("CHAT_IMAGE_TEST_REDIS_SOCKET")
    if not socket:
        pytest.skip("Explicit isolated Redis socket required")
    assert socket == "/private/tmp/everydayai-chat-image-redis.sock"
    redis = Redis(unix_socket_path=socket, decode_responses=True)
    user = "isolated-" + uuid4().hex
    service = TaskLimitService(redis)
    service.global_limit = service.conversation_limit = 15
    accepted = []
    async def attempt(index):
        conversation = f"conv-{index % 2}"
        try:
            slot = (await service.check_and_acquire(user, conversation) if index % 3 == 0
                    else await service.acquire_image_slot(user, conversation, f"image-{index}"))
            return conversation, slot
        except TaskQueueFullError:
            return None
    try:
        results = await asyncio.gather(*(attempt(i) for i in range(80)))
        accepted = [result for result in results if result]
        assert len(accepted) == 15
        assert await redis.scard(service._global_key(user)) == 15
        for conversation, slot in accepted:
            await service.release(user, conversation, slot_id=slot)
            await service.release(user, conversation, slot_id=slot)
        assert await redis.scard(service._global_key(user)) == 0
        # A single conversation can use all15, and completed count never exhausts it.
        for batch in range(3):
            slots = await asyncio.gather(*(service.acquire_image_slot(user,"conv-0",f"next-{batch}-{i}") for i in range(15)))
            assert await redis.scard(service._global_key(user)) == 15
            for slot in slots: await service.release(user,"conv-0",slot_id=slot)
    finally:
        await redis.delete(service._global_key(user),service._conversation_key(user,"conv-0"),service._conversation_key(user,"conv-1"))
        await redis.aclose()
