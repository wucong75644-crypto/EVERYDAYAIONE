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
async def test_pre_send_unavailable_is_not_started_and_read_only():
    client = WecomWSClient("bot", "secret")
    first = await client.send_proactive_typed(
        "provider-offline", "chat", "text", {"content": "safe"},
    )
    duplicate = await client.send_proactive_typed(
        "provider-offline", "chat", "text", {"content": "safe"},
    )

    assert first.status == WecomOutboundStatus.NOT_STARTED
    assert first.error_class == WecomOutboundErrorClass.UNAVAILABLE
    assert duplicate == first
    assert client._outbound_requests["provider-offline"].write_started is False


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
