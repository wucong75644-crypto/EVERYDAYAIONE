"""Local mock coverage for typed WeCom Smart Robot outbound ACK transport."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom import ws_outbound
from services.wecom.ws_client import WecomWSClient
from services.wecom.ws_outbound import (
    WecomOutboundErrorClass,
    WecomOutboundStatus,
)


@pytest.fixture
def client() -> WecomWSClient:
    instance = WecomWSClient("bot", "secret")
    instance._ws = AsyncMock()
    instance._is_connected = True
    return instance


def _ack(client: WecomWSClient, req_id: str, errcode: int, **extra: object) -> bool:
    return client._route_typed_outbound_ack({
        "headers": {"req_id": req_id},
        "errcode": errcode,
        **extra,
    })


@pytest.mark.parametrize("provider_request_id", [
    "short7", "unsafe/id", "ping_12345678", "x" * 201,
])
def test_readback_invalid_or_missing_identity_returns_none_without_send(
    client: WecomWSClient, provider_request_id: str,
) -> None:
    assert client.lookup_outbound_result(provider_request_id) is None
    assert client.lookup_outbound_result("provider-missing") is None
    client._ws.send.assert_not_called()


@pytest.mark.asyncio
async def test_readback_pending_returns_immediately_without_extra_send(client):
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def send(_: str) -> None:
        send_started.set()
        await release_send.wait()

    client._ws.send = AsyncMock(side_effect=send)
    task = asyncio.create_task(client.send_proactive_typed(
        "provider-pending", "chat", "text", {"content": "safe"},
    ))
    await send_started.wait()

    assert client.lookup_outbound_result("provider-pending") is None
    assert task.done() is False
    assert client._ws.send.await_count == 1

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_readback_returns_unknown_then_late_definitive_results(
    monkeypatch, client,
):
    monkeypatch.setattr(ws_outbound, "OUTBOUND_ACK_TIMEOUT", 0.01)
    unknown = await client.send_proactive_typed(
        "provider-readback-ack", "chat", "text", {"content": "safe"},
    )
    assert client.lookup_outbound_result("provider-readback-ack") == unknown

    assert _ack(client, "provider-readback-ack", 0) is True
    client._is_connected = False
    acknowledged = client.lookup_outbound_result("provider-readback-ack")
    assert acknowledged is not None
    assert acknowledged.status is WecomOutboundStatus.ACKNOWLEDGED

    client._is_connected = True
    rejected_unknown = await client.send_proactive_typed(
        "provider-readback-reject", "chat", "text", {"content": "safe"},
    )
    assert rejected_unknown.status is WecomOutboundStatus.UNKNOWN
    assert _ack(client, "provider-readback-reject", 41013) is True
    rejected = client.lookup_outbound_result("provider-readback-reject")
    assert rejected is not None
    assert rejected.status is WecomOutboundStatus.REJECTED
    assert rejected.errcode == 41013
    assert client._ws.send.await_count == 2


@pytest.mark.asyncio
async def test_readback_prunes_expired_result_without_refreshing_ttl(
    monkeypatch, client,
):
    async def send(raw: str) -> None:
        _ack(client, json.loads(raw)["headers"]["req_id"], 0)

    client._ws.send = AsyncMock(side_effect=send)
    await client.send_proactive_typed(
        "provider-expired", "chat", "text", {"content": "safe"},
    )
    entry = client._outbound_requests["provider-expired"]
    original_updated_at = entry.updated_at
    monkeypatch.setattr(
        ws_outbound.time, "monotonic",
        lambda: original_updated_at + ws_outbound.OUTBOUND_RESULT_TTL - 1,
    )
    assert client.lookup_outbound_result("provider-expired") is entry.result
    assert entry.updated_at == original_updated_at

    monkeypatch.setattr(
        ws_outbound.time, "monotonic",
        lambda: original_updated_at + ws_outbound.OUTBOUND_RESULT_TTL,
    )
    assert client.lookup_outbound_result("provider-expired") is None
    assert "provider-expired" not in client._outbound_requests
    assert client._ws.send.await_count == 1


@pytest.mark.asyncio
async def test_ack_success_registers_before_send_and_uses_provider_id(client):
    async def send(raw: str) -> None:
        payload = json.loads(raw)
        req_id = payload["headers"]["req_id"]
        assert req_id in client._outbound_requests
        assert client._outbound_requests[req_id].result is None
        assert payload["body"]["chatid"] == "chat-redacted"
        _ack(client, req_id, 0, errmsg="raw-provider-message")

    client._ws.send = send
    result = await client.send_proactive_typed(
        "provider-001", "chat-redacted", "text", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.ACKNOWLEDGED
    assert result.provider_request_id == "provider-001"
    assert result.errcode is None
    assert result.error_class is None
    assert "raw-provider-message" not in repr(result)
    assert client._outbound_requests["provider-001"].future is None


@pytest.mark.asyncio
async def test_nonzero_ack_is_typed_rejection_without_errmsg(client):
    async def send(raw: str) -> None:
        req_id = json.loads(raw)["headers"]["req_id"]
        _ack(client, req_id, 41013, errmsg="secret token raw payload")

    client._ws.send = send
    result = await client.send_proactive_typed(
        "provider-reject", "chat", "markdown", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.REJECTED
    assert result.errcode == 41013
    assert result.error_class == WecomOutboundErrorClass.PROVIDER_REJECTED
    assert "secret" not in repr(result)
    assert "raw payload" not in repr(result)


@pytest.mark.asyncio
async def test_unavailable_then_reconnect_same_id_sends_once_and_acks():
    client = WecomWSClient("bot", "secret")
    first = await client.send_proactive_typed(
        "provider-offline", "chat", "text", {"content": "safe"},
    )
    assert first.status == WecomOutboundStatus.NOT_STARTED
    assert first.error_class == WecomOutboundErrorClass.UNAVAILABLE
    assert "provider-offline" not in client._outbound_requests

    client._ws = AsyncMock()
    client._is_connected = True

    async def send(raw: str) -> None:
        _ack(client, json.loads(raw)["headers"]["req_id"], 0)

    client._ws.send.side_effect = send
    reconnected = await client.send_proactive_typed(
        "provider-offline", "chat", "text", {"content": "safe"},
    )
    duplicate = await client.send_proactive_typed(
        "provider-offline", "chat", "text", {"content": "safe"},
    )

    assert reconnected.status == WecomOutboundStatus.ACKNOWLEDGED
    assert duplicate == reconnected
    client._ws.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_unavailable_calls_are_retry_safe():
    client = WecomWSClient("bot", "secret")
    results = await asyncio.gather(*(
        client.send_proactive_typed(
            "provider-unavailable", "chat", "text", {"content": "safe"},
        )
        for _ in range(50)
    ))

    assert all(result.status == WecomOutboundStatus.NOT_STARTED for result in results)
    assert all(
        result.error_class == WecomOutboundErrorClass.UNAVAILABLE
        for result in results
    )
    assert client._outbound_requests == {}


@pytest.mark.asyncio
async def test_heartbeat_reserved_identity_fails_before_registration(client):
    result = await client.send_proactive_typed(
        "ping_conflict", "chat", "text", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.NOT_STARTED
    assert result.error_class == WecomOutboundErrorClass.INVALID_REQUEST
    assert "ping_conflict" not in client._outbound_requests
    client._ws.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_request_id", [
    "short7",
    "unsafe/id",
    "unsafe id",
    "请求标识-12345678",
    "x" * 201,
])
async def test_provider_identity_rejects_unsafe_or_out_of_range(
    client, provider_request_id,
):
    result = await client.send_proactive_typed(
        provider_request_id, "chat", "text", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.NOT_STARTED
    assert result.error_class == WecomOutboundErrorClass.INVALID_REQUEST
    assert provider_request_id not in client._outbound_requests
    client._ws.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_request_id", ["safe-001", "x" * 200])
async def test_provider_identity_accepts_length_boundaries(provider_request_id):
    client = WecomWSClient("bot", "secret")
    result = await client.send_proactive_typed(
        provider_request_id, "chat", "text", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.NOT_STARTED
    assert result.error_class == WecomOutboundErrorClass.UNAVAILABLE
    assert provider_request_id not in client._outbound_requests


@pytest.mark.asyncio
async def test_post_send_timeout_is_unknown(monkeypatch, client):
    monkeypatch.setattr(ws_outbound, "OUTBOUND_ACK_TIMEOUT", 0.01)
    result = await client.send_proactive_typed(
        "provider-timeout", "chat", "text", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.UNKNOWN
    assert result.error_class == WecomOutboundErrorClass.ACK_TIMEOUT
    assert client._outbound_requests["provider-timeout"].future is None


@pytest.mark.asyncio
async def test_disconnect_after_write_is_unknown(client):
    async def send(_: str) -> None:
        client._mark_typed_outbound_disconnected()

    client._ws.send = send
    result = await client.send_proactive_typed(
        "provider-disconnect", "chat", "text", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.UNKNOWN
    assert result.error_class == WecomOutboundErrorClass.DISCONNECTED


@pytest.mark.asyncio
async def test_write_failure_is_unknown_and_closes_without_error_text(client):
    client._ws.send.side_effect = RuntimeError("secret raw provider failure")
    result = await client.send_proactive_typed(
        "provider-write-fail", "chat", "text", {"content": "safe"},
    )

    assert result.status == WecomOutboundStatus.UNKNOWN
    assert result.error_class == WecomOutboundErrorClass.TRANSPORT_INTERRUPTED
    assert "secret raw provider failure" not in repr(result)
    assert client._is_connected is False
    client._ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_ack_upgrades_unknown_and_duplicate_does_not_resend(
    monkeypatch, client,
):
    monkeypatch.setattr(ws_outbound, "OUTBOUND_ACK_TIMEOUT", 0.01)
    first = await client.send_proactive_typed(
        "provider-late", "chat", "text", {"content": "safe"},
    )
    assert first.status == WecomOutboundStatus.UNKNOWN
    assert client._ws.send.await_count == 1

    assert _ack(client, "provider-late", 0) is True
    readback = await client.send_proactive_typed(
        "provider-late", "chat", "text", {"content": "safe"},
    )
    assert readback.status == WecomOutboundStatus.ACKNOWLEDGED
    assert client._ws.send.await_count == 1


@pytest.mark.asyncio
async def test_fifty_concurrent_requests_are_isolated(client):
    async def send(raw: str) -> None:
        req_id = json.loads(raw)["headers"]["req_id"]
        await asyncio.sleep(0)
        _ack(client, req_id, 0)

    client._ws.send = send
    results = await asyncio.gather(*(
        client.send_proactive_typed(
            f"provider-{index}",
            f"chat-{index}",
            "text",
            {"content": f"safe-{index}"},
        )
        for index in range(50)
    ))

    assert {result.provider_request_id for result in results} == {
        f"provider-{index}" for index in range(50)
    }
    assert all(
        result.status == WecomOutboundStatus.ACKNOWLEDGED for result in results
    )


@pytest.mark.asyncio
async def test_duplicate_shares_pending_and_conflict_fails_closed(client):
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    send_count = 0

    async def send(_: str) -> None:
        nonlocal send_count
        send_count += 1
        send_started.set()
        await release_send.wait()

    client._ws.send = send
    first_task = asyncio.create_task(client.send_proactive_typed(
        "provider-duplicate", "chat", "text", {"content": "same"},
    ))
    await send_started.wait()
    duplicate_task = asyncio.create_task(client.send_proactive_typed(
        "provider-duplicate", "chat", "text", {"content": "same"},
    ))
    await asyncio.sleep(0)
    assert send_count == 1
    release_send.set()
    await asyncio.sleep(0)
    _ack(client, "provider-duplicate", 0)

    first, duplicate = await asyncio.gather(first_task, duplicate_task)
    conflict = await client.send_proactive_typed(
        "provider-duplicate", "chat", "text", {"content": "different"},
    )
    assert first == duplicate
    assert conflict.status == WecomOutboundStatus.NOT_STARTED
    assert conflict.error_class == WecomOutboundErrorClass.IDENTITY_CONFLICT
    assert send_count == 1


@pytest.mark.asyncio
async def test_heartbeat_ack_does_not_settle_typed_request(client):
    send_started = asyncio.Event()

    async def send(_: str) -> None:
        send_started.set()

    client._ws.send = send
    task = asyncio.create_task(client.send_proactive_typed(
        "provider-heartbeat", "chat", "text", {"content": "safe"},
    ))
    await send_started.wait()
    frames = [
        json.dumps({"headers": {"req_id": "ping_123"}, "errcode": 0}),
        json.dumps({
            "headers": {"req_id": "provider-heartbeat"}, "errcode": 0,
        }),
    ]
    client._ws.__aiter__ = lambda value: value

    async def next_frame(_: object) -> str:
        if frames:
            return frames.pop(0)
        raise StopAsyncIteration

    client._ws.__anext__ = next_frame
    await client._receive_loop()
    result = await task

    assert client._hb_acked == 1
    assert result.status == WecomOutboundStatus.ACKNOWLEDGED


@pytest.mark.asyncio
async def test_cancel_cleans_future_and_late_ack_remains_readable(client):
    send_started = asyncio.Event()

    async def send(_: str) -> None:
        send_started.set()

    client._ws.send = send
    task = asyncio.create_task(client.send_proactive_typed(
        "provider-cancel", "chat", "text", {"content": "safe"},
    ))
    await send_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    entry = client._outbound_requests["provider-cancel"]
    assert entry.result.status == WecomOutboundStatus.UNKNOWN
    assert entry.result.error_class == WecomOutboundErrorClass.CANCELLED
    assert entry.future is None
    _ack(client, "provider-cancel", 0)
    readback = await client.send_proactive_typed(
        "provider-cancel", "chat", "text", {"content": "safe"},
    )
    assert readback.status == WecomOutboundStatus.ACKNOWLEDGED


@pytest.mark.asyncio
async def test_completed_registry_is_bounded(monkeypatch, client):
    monkeypatch.setattr(ws_outbound, "OUTBOUND_RESULT_CAPACITY", 2)

    async def send(raw: str) -> None:
        _ack(client, json.loads(raw)["headers"]["req_id"], 0)

    client._ws.send = send
    for index in range(3):
        await client.send_proactive_typed(
            f"bounded-{index}", "chat", "text", {"content": str(index)},
        )

    assert list(client._outbound_requests) == ["bounded-1", "bounded-2"]
    assert all(entry.future is None for entry in client._outbound_requests.values())


@pytest.mark.asyncio
async def test_pending_registry_rejects_excess_and_cancel_cleans(monkeypatch, client):
    monkeypatch.setattr(ws_outbound, "OUTBOUND_PENDING_CAPACITY", 2)
    release_send = asyncio.Event()

    async def send(_: str) -> None:
        await release_send.wait()

    client._ws.send = send
    pending = [
        asyncio.create_task(client.send_proactive_typed(
            f"pending-{index}", "chat", "text", {"content": str(index)},
        ))
        for index in range(2)
    ]
    await asyncio.sleep(0)
    excess = await client.send_proactive_typed(
        "pending-excess", "chat", "text", {"content": "excess"},
    )
    assert excess.status == WecomOutboundStatus.NOT_STARTED
    assert excess.error_class == WecomOutboundErrorClass.CAPACITY_EXCEEDED
    assert "pending-excess" not in client._outbound_requests

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    assert all(entry.future is None for entry in client._outbound_requests.values())
