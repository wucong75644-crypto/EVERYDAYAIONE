"""Real Redis Standalone contracts required by confirmation challenges."""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, ResponseError, TimeoutError

from tests.redis_external import RedisExternalHarness
from services.tool_confirmation.redis_store import (
    ToolConfirmationRedisStore, hash_waiter_token,
)
from services.tool_confirmation.types import ConfirmationBinding


pytestmark = pytest.mark.external
pytest_plugins = ("tests.redis_external",)

_CAS_SCRIPT = """
local state = redis.call("HGET", KEYS[1], "state")
if not state then
    return "NOT_FOUND"
end
if state ~= "PENDING" then
    return "SEEN:" .. state
end
local now = redis.call("TIME")
local now_ms = (now[1] * 1000) + math.floor(now[2] / 1000)
local expires_at_ms = tonumber(redis.call("HGET", KEYS[1], "expires_at_ms"))
if not expires_at_ms then
    return redis.error_reply("MISSING_EXPIRY")
end
local target = ARGV[1]
if now_ms >= expires_at_ms then
    redis.call("HSET", KEYS[1], "state", "EXPIRED")
    redis.call("EXPIRE", KEYS[1], ARGV[2])
    return "WON:EXPIRED"
end
if target == "EXPIRED" then
    return "NOT_EXPIRED"
end
if target ~= "APPROVED" and target ~= "DENIED" then
    return redis.error_reply("INVALID_TARGET")
end
redis.call("HSET", KEYS[1], "state", target)
redis.call("EXPIRE", KEYS[1], ARGV[2])
return "WON:" .. target
"""


async def _redis_time_ms(client: Redis) -> int:
    seconds, micros = await client.time()
    return (seconds * 1000) + (micros // 1000)


async def _pending(
    harness: RedisExternalHarness,
    suffix: str,
    *,
    lifetime_ms: int = 10_000,
) -> str:
    key = harness.key(suffix)
    expires_at_ms = await _redis_time_ms(harness.client) + lifetime_ms
    await harness.client.hset(
        key,
        mapping={"state": "PENDING", "expires_at_ms": expires_at_ms},
    )
    await harness.client.expire(key, 30)
    return key


@pytest.mark.asyncio
async def test_ping_nx_ttl_time_eval_and_shared_clients(
    redis_external: RedisExternalHarness,
) -> None:
    client = redis_external.client
    key = redis_external.key("basic")
    assert await client.ping() is True
    assert await client.set(key, "first", nx=True, ex=10) is True
    assert await client.set(key, "second", nx=True, ex=10) is None
    assert 0 < await client.ttl(key) <= 10
    seconds, micros = await client.time()
    assert seconds > 0
    assert 0 <= micros < 1_000_000
    assert await client.eval(
        "return redis.call('GET', KEYS[1])", 1, key,
    ) == "first"

    second = redis_external.new_client()
    try:
        assert await second.get(key) == "first"
        await second.set(key, "shared")
        assert await client.get(key) == "shared"
    finally:
        await second.aclose()


@pytest.mark.asyncio
async def test_lua_time_compare_and_set_has_one_terminal_winner(
    redis_external: RedisExternalHarness,
) -> None:
    key = await _pending(redis_external, "race", lifetime_ms=1)
    await asyncio.sleep(0.005)
    second = redis_external.new_client()
    try:
        results = await asyncio.gather(
            redis_external.client.eval(
                _CAS_SCRIPT, 1, key, "APPROVED", 10,
            ),
            second.eval(_CAS_SCRIPT, 1, key, "DENIED", 10),
            redis_external.client.eval(
                _CAS_SCRIPT, 1, key, "EXPIRED", 10,
            ),
        )
        assert sum(result.startswith("WON:") for result in results) == 1
        assert all(result.endswith("EXPIRED") for result in results)
        assert await redis_external.client.hget(key, "state") == "EXPIRED"
    finally:
        await second.aclose()


@pytest.mark.asyncio
async def test_concurrent_approve_and_deny_cannot_both_win(
    redis_external: RedisExternalHarness,
) -> None:
    key = await _pending(redis_external, "decision-race")
    second = redis_external.new_client()
    try:
        results = await asyncio.gather(
            redis_external.client.eval(
                _CAS_SCRIPT, 1, key, "APPROVED", 10,
            ),
            second.eval(_CAS_SCRIPT, 1, key, "DENIED", 10),
        )
        assert sum(result.startswith("WON:") for result in results) == 1
        terminal = await redis_external.client.hget(key, "state")
        assert terminal in {"APPROVED", "DENIED"}
        assert all(result.endswith(terminal) for result in results)
    finally:
        await second.aclose()


@pytest.mark.asyncio
async def test_duplicate_and_flipped_response_cannot_change_terminal_state(
    redis_external: RedisExternalHarness,
) -> None:
    key = await _pending(redis_external, "duplicate")
    first = await redis_external.client.eval(
        _CAS_SCRIPT, 1, key, "APPROVED", 10,
    )
    duplicate = await redis_external.client.eval(
        _CAS_SCRIPT, 1, key, "APPROVED", 10,
    )
    flipped = await redis_external.client.eval(
        _CAS_SCRIPT, 1, key, "DENIED", 10,
    )
    assert (first, duplicate, flipped) == (
        "WON:APPROVED",
        "SEEN:APPROVED",
        "SEEN:APPROVED",
    )


@pytest.mark.asyncio
async def test_expire_requires_redis_time_to_reach_deadline(
    redis_external: RedisExternalHarness,
) -> None:
    key = await _pending(redis_external, "expiry", lifetime_ms=80)
    assert await redis_external.client.eval(
        _CAS_SCRIPT, 1, key, "EXPIRED", 10,
    ) == "NOT_EXPIRED"
    await asyncio.sleep(0.1)
    assert await redis_external.client.eval(
        _CAS_SCRIPT, 1, key, "EXPIRED", 10,
    ) == "WON:EXPIRED"


@pytest.mark.asyncio
async def test_redis_ttl_deletes_key(
    redis_external: RedisExternalHarness,
) -> None:
    key = redis_external.key("ttl")
    await redis_external.client.set(key, "temporary", px=40)
    await asyncio.sleep(0.08)
    assert await redis_external.client.get(key) is None


@pytest.mark.asyncio
async def test_script_errors_are_not_treated_as_success(
    redis_external: RedisExternalHarness,
) -> None:
    with pytest.raises(ResponseError):
        await redis_external.client.eval(
            "return redis.error_reply('CONTRACT_FAILURE')", 0,
        )


@pytest.mark.asyncio
async def test_connection_timeout_is_an_explicit_failure(
    redis_external: RedisExternalHarness,
) -> None:
    impatient = Redis.from_url(
        redis_external.url,
        socket_connect_timeout=0.05,
        socket_timeout=0.05,
    )
    try:
        await redis_external.client.client_pause(150)
        with pytest.raises(TimeoutError):
            await impatient.ping()
    finally:
        await asyncio.sleep(0.16)
        await impatient.aclose()


@pytest.mark.asyncio
async def test_wrong_url_fails_closed() -> None:
    client = Redis.from_url(
        "redis://127.0.0.1:1/15",
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
    )
    try:
        with pytest.raises(ConnectionError):
            await client.ping()
    finally:
        await client.aclose()


@pytest.fixture
def v3_store(redis_external: RedisExternalHarness, monkeypatch):
    from core.redis import RedisClient
    import services.tool_confirmation.redis_store as store_module

    monkeypatch.setattr(RedisClient, "_instance", redis_external.client)
    monkeypatch.setattr(
        store_module, "KEY_PREFIX",
        f"{redis_external.namespace}{{tool-confirm}}",
    )
    return ToolConfirmationRedisStore()


def _binding(arguments_hash: str = "args-hash") -> ConfirmationBinding:
    return ConfirmationBinding(
        "task-1", "call-1", "restore_file", arguments_hash, "user-1", "org-1",
    )


@pytest.mark.asyncio
async def test_v3_same_identity_fifty_creates_has_one_challenge(
    redis_external: RedisExternalHarness,
    v3_store: ToolConfirmationRedisStore,
) -> None:
    results = await asyncio.gather(*[
        v3_store.create(f"confirmation-{index}", _binding(), hash_waiter_token(f"waiter-{index}"))
        for index in range(50)
    ])
    assert results.count("CREATED:PENDING") == 1
    assert results.count("CREATE_CONFLICT") == 49
    challenge_keys = [
        key async for key in redis_external.client.scan_iter(
            match=f"{redis_external.namespace}*challenge:*",
        )
    ]
    assert len(challenge_keys) == 1


@pytest.mark.asyncio
async def test_v3_create_retry_is_strictly_idempotent(
    v3_store: ToolConfirmationRedisStore,
) -> None:
    waiter_hash = hash_waiter_token("waiter")
    first = await v3_store.create("confirmation", _binding(), waiter_hash)
    retry = await v3_store.create("confirmation", _binding(), waiter_hash)
    changed = await v3_store.create("confirmation", _binding("changed"), waiter_hash)
    assert first == "CREATED:PENDING"
    assert retry == "IDEMPOTENT:PENDING"
    assert changed == "MALFORMED_STATE"


@pytest.mark.asyncio
async def test_v3_decision_and_claim_are_single_winner(
    v3_store: ToolConfirmationRedisStore,
) -> None:
    binding = _binding()
    waiter_hash = hash_waiter_token("waiter")
    assert await v3_store.create("confirmation", binding, waiter_hash) == "CREATED:PENDING"
    decisions = await asyncio.gather(
        v3_store.consume("confirmation", binding, "user-1", "org-1", True),
        v3_store.consume("confirmation", binding, "user-1", "org-1", False),
    )
    assert sum(value.startswith("WON:") for value in decisions) == 1
    record = await v3_store.read("confirmation", binding)
    if record["state"] == "APPROVED":
        claims = await asyncio.gather(
            v3_store.claim("confirmation", binding, waiter_hash),
            v3_store.claim("confirmation", binding, waiter_hash),
            v3_store.claim("confirmation", binding, hash_waiter_token("wrong")),
        )
        assert claims.count("WON:EXECUTION_CLAIMED") == 1
        assert (await v3_store.read("confirmation", binding))["state"] == "EXECUTION_CLAIMED"
    else:
        assert record["state"] == "DENIED"


@pytest.mark.asyncio
async def test_v3_claim_window_expiry_cannot_be_reclaimed(
    v3_store: ToolConfirmationRedisStore,
    monkeypatch,
) -> None:
    import services.tool_confirmation.redis_store as store_module

    monkeypatch.setattr(store_module, "CLAIM_SECONDS", 1)
    binding = _binding()
    waiter_hash = hash_waiter_token("waiter")
    assert await v3_store.create("confirmation", binding, waiter_hash) == "CREATED:PENDING"
    assert await v3_store.consume(
        "confirmation", binding, "user-1", "org-1", True,
    ) == "WON:APPROVED"
    await asyncio.sleep(1.05)
    assert await v3_store.claim(
        "confirmation", binding, waiter_hash,
    ) == "WON:EXPIRED"
    assert await v3_store.claim(
        "confirmation", binding, waiter_hash,
    ) == "NOT_APPROVED:EXPIRED"


@pytest.mark.asyncio
async def test_v3_cross_worker_notification_requires_delivery_ack(
    redis_external: RedisExternalHarness,
    v3_store: ToolConfirmationRedisStore,
    monkeypatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import services.websocket_redis as redis_module
    from services.websocket_manager import WebSocketManager

    channel = redis_external.key("ws-channel")
    monkeypatch.setattr(redis_module, "WS_CHANNEL", channel)
    sender = WebSocketManager()
    receiver = WebSocketManager()
    receiver._connections = {
        "user-1": {
            "conn-1": SimpleNamespace(org_id="org-1"),
        },
    }
    receiver.send_to_connection = AsyncMock(return_value=True)
    receiver_client = redis_external.new_client()
    receiver._pubsub_redis = receiver_client
    receiver._pubsub = receiver_client.pubsub()
    await receiver._pubsub.subscribe(channel)
    receiver._listener_task = asyncio.create_task(
        receiver._redis_listen_loop(),
    )
    try:
        delivered = await sender._publish_with_delivery_ack(
            "user-1",
            {
                "type": "tool_confirm_request",
                "payload": {"confirmation_id": "opaque"},
            },
            org_id="org-1",
        )
        assert delivered is True
        receiver.send_to_connection.assert_awaited_once()
    finally:
        receiver._listener_task.cancel()
        await receiver._listener_task
        await receiver._pubsub.unsubscribe(channel)
        await receiver._pubsub.aclose()
        await receiver_client.aclose()
