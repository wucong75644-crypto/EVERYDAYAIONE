"""Fail-closed production Redis capability probe for Confirmation V3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from core.redis import RedisClient
from .redis_store import (
    ToolConfirmationRedisStore, confirmation_keys, hash_waiter_token,
)
from .types import ConfirmationBinding


@dataclass(frozen=True)
class RedisCapabilityProbe:
    ready: bool
    code: str


async def probe_tool_confirmation_redis() -> RedisCapabilityProbe:
    identity = str(uuid4())
    binding = ConfirmationBinding(
        action_id=str(uuid4()), interaction_id=str(uuid4()),
        interaction_version=0, task_id=str(uuid4()),
        tool_call_id=f"probe-{identity}", tool_name="probe",
        arguments_hash="0" * 64, user_id=str(uuid4()), org_id="",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    store = ToolConfirmationRedisStore()
    client = await RedisClient.get_client()
    keys = confirmation_keys(binding, identity)
    token = hash_waiter_token(identity)
    try:
        if not await client.ping():
            return RedisCapabilityProbe(False, "REDIS_PING_FAILED")
        await client.time()
        if await store.create(identity, binding, token) != "CREATED:PENDING":
            return RedisCapabilityProbe(False, "REDIS_CREATE_FAILED")
        if await store.consume(
            identity, binding, binding.user_id, "", True,
        ) != "WON:APPROVED":
            return RedisCapabilityProbe(False, "REDIS_CONSUME_FAILED")
        if await store.claim(identity, binding, token) != "WON:EXECUTION_CLAIMED":
            return RedisCapabilityProbe(False, "REDIS_CLAIM_FAILED")
        if (await store.read(identity, binding)).get("state") != "EXECUTION_CLAIMED":
            return RedisCapabilityProbe(False, "REDIS_READBACK_FAILED")
        await client.rpush(keys[2], "probe")
        if not await client.blpop(keys[2], timeout=1):
            return RedisCapabilityProbe(False, "REDIS_BLOCKING_LIST_FAILED")
        return RedisCapabilityProbe(True, "ok")
    except Exception as error:
        return RedisCapabilityProbe(
            False, f"REDIS_CAPABILITY_{type(error).__name__}".upper(),
        )
    finally:
        await client.delete(*keys)
