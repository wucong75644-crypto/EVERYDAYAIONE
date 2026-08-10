"""Typed WeCom App HTTP outbound transport tests."""

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom.app_outbound import (
    REQUEST_ID_HEADER,
    WecomAppOutbound,
    WecomAppOutboundErrorClass,
    WecomAppOutboundStatus,
)


def _payload(
    target: str = "user-001",
    content: str = "safe content",
) -> dict[str, Any]:
    return {
        "touser": target,
        "msgtype": "text",
        "agentid": 1000,
        "text": {"content": content},
    }


def _response(data: object, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = data
    return response


def _sender(
    *,
    token_provider: AsyncMock | None = None,
    client: AsyncMock | None = None,
    capacity: int = 1024,
) -> tuple[WecomAppOutbound, AsyncMock, AsyncMock]:
    provider = token_provider or AsyncMock(return_value="token-value")
    http_client = client or AsyncMock()
    http_client.is_closed = False
    if client is None:
        http_client.post.return_value = _response({"errcode": 0})
    return (
        WecomAppOutbound(
            token_provider=provider,
            http_client=http_client,
            capacity=capacity,
        ),
        provider,
        http_client,
    )


@pytest.mark.asyncio
async def test_explicit_injection_acknowledges_and_correlates_request() -> None:
    sender, token_provider, client = _sender()
    client.post.return_value = _response({"errcode": 0, "msgid": "msg-001"})

    result = await sender.send_typed(
        provider_request_id="request-001",
        target="user-001",
        payload=_payload(),
    )

    assert result.status is WecomAppOutboundStatus.ACKNOWLEDGED
    assert result.errcode == 0
    assert result.provider_message_id == "msg-001"
    token_provider.assert_awaited_once_with()
    assert client.post.await_args.kwargs["headers"] == {
        REQUEST_ID_HEADER: "request-001",
    }
    assert client.post.await_args.kwargs["params"] == {
        "access_token": "token-value",
    }


@pytest.mark.asyncio
async def test_non_2xx_cannot_ack_even_with_errcode_zero() -> None:
    sender, _, client = _sender()
    response = _response({"errcode": 0}, status_code=503)
    client.post.return_value = response
    result = await sender.send_typed(
        provider_request_id="request-http-503",
        target="user-001",
        payload=_payload(),
    )
    assert result.status is WecomAppOutboundStatus.UNKNOWN
    assert result.error_class is WecomAppOutboundErrorClass.HTTP_STATUS_AMBIGUOUS
    response.json.assert_not_called()


@pytest.mark.asyncio
async def test_no_credential_is_not_started_and_same_id_can_retry() -> None:
    tokens = [None, "later-token"]

    async def token_provider() -> str | None:
        return tokens.pop(0)
    client = AsyncMock()
    client.is_closed = False
    client.post.return_value = _response({"errcode": 0})
    sender = WecomAppOutbound(
        token_provider=token_provider,
        http_client=client,
    )
    first = await sender.send_typed(
        provider_request_id="request-002",
        target="user-001",
        payload=_payload(),
    )
    second = await sender.send_typed(
        provider_request_id="request-002",
        target="user-001",
        payload=_payload(),
    )
    assert first.status is WecomAppOutboundStatus.NOT_STARTED
    assert first.error_class is WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE
    assert second.status is WecomAppOutboundStatus.ACKNOWLEDGED
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_capacity_never_evicts_completed_identity() -> None:
    sender, token_provider, client = _sender(capacity=1)
    first = await sender.send_typed(
        provider_request_id="capacity-A",
        target="user-001",
        payload=_payload(),
    )
    blocked = await sender.send_typed(
        provider_request_id="capacity-B",
        target="user-001",
        payload=_payload(content="B"),
    )
    readback = await sender.send_typed(
        provider_request_id="capacity-A",
        target="user-001",
        payload=_payload(),
    )
    assert first.status is WecomAppOutboundStatus.ACKNOWLEDGED
    assert blocked.status is WecomAppOutboundStatus.NOT_STARTED
    assert blocked.error_class is WecomAppOutboundErrorClass.CAPACITY_EXCEEDED
    assert readback == first
    token_provider.assert_awaited_once()
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_payload_is_snapshotted_before_credential_await() -> None:
    resolving = asyncio.Event()
    release = asyncio.Event()

    async def token_provider() -> str:
        resolving.set()
        await release.wait()
        return "token"

    client = AsyncMock()
    client.is_closed = False
    client.post.return_value = _response({"errcode": 0})
    sender = WecomAppOutbound(token_provider=token_provider, http_client=client)
    payload = _payload(content="original")
    task = asyncio.create_task(sender.send_typed(
        provider_request_id="snapshot-001",
        target="user-001",
        payload=payload,
    ))
    await resolving.wait()
    payload["text"]["content"] = "mutated"
    payload["access_token"] = "injected-secret"
    release.set()
    result = await task
    assert result.status is WecomAppOutboundStatus.ACKNOWLEDGED
    sent = client.post.await_args.kwargs["json"]
    assert sent["text"]["content"] == "original"
    assert "access_token" not in sent


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["credential", "credential_error", "post"])
async def test_transport_enforces_failure_deadlines(phase: str) -> None:
    never = asyncio.Event()
    if phase == "credential":
        token_provider = lambda: never.wait()
    elif phase == "credential_error":
        token_provider = AsyncMock(side_effect=RuntimeError("private credential"))
    else:
        token_provider = AsyncMock(return_value="token")

    async def post(*args: object, **kwargs: object) -> None:
        await never.wait()

    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = post
    sender = WecomAppOutbound(
        token_provider=token_provider,
        http_client=client,
        credential_timeout=0.01,
        post_timeout=0.01,
    )
    result = await sender.send_typed(
        provider_request_id=f"deadline-{phase}",
        target="user-001",
        payload=_payload(),
    )
    assert result.status is {
        "credential": WecomAppOutboundStatus.NOT_STARTED,
        "credential_error": WecomAppOutboundStatus.NOT_STARTED,
        "post": WecomAppOutboundStatus.UNKNOWN,
    }[phase]
    assert result.error_class is {
        "credential": WecomAppOutboundErrorClass.CREDENTIAL_TIMEOUT,
        "credential_error": WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE,
        "post": WecomAppOutboundErrorClass.POST_TIMEOUT,
    }[phase]
    assert client.post.await_count == (1 if phase == "post" else 0)


@pytest.mark.asyncio
async def test_missing_or_closed_transport_is_not_started() -> None:
    missing = WecomAppOutbound(token_provider=None, http_client=None)
    missing_result = await missing.send_typed(
        provider_request_id="request-003",
        target="user-001",
        payload=_payload(),
    )
    closed_client = AsyncMock()
    closed_client.is_closed = True
    closed = WecomAppOutbound(
        token_provider=AsyncMock(return_value="token"),
        http_client=closed_client,
    )
    closed_result = await closed.send_typed(
        provider_request_id="request-004",
        target="user-001",
        payload=_payload(),
    )
    assert missing_result.status is WecomAppOutboundStatus.NOT_STARTED
    assert closed_result.status is WecomAppOutboundStatus.NOT_STARTED
    assert closed_result.error_class is WecomAppOutboundErrorClass.TRANSPORT_UNAVAILABLE
    closed_client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_request_id", "target", "payload"),
    [
        ("short", "user-001", _payload()),
        ("unsafe id", "user-001", _payload()),
        ("request-005", "", _payload()),
        ("request-006", "other-user", _payload()),
        ("request-007", "user-001", {"touser": "user-001"}),
        (
            "request-008",
            "user-001",
            {**_payload(), "access_token": "must-not-be-payload"},
        ),
        (
            "request-009",
            "user-001",
            {**_payload(), "text": {"content": object()}},
        ),
    ],
)
async def test_invalid_identity_target_or_payload_is_not_started(
    provider_request_id: str,
    target: str,
    payload: dict[str, Any],
) -> None:
    sender, token_provider, client = _sender()

    result = await sender.send_typed(
        provider_request_id=provider_request_id,
        target=target,
        payload=payload,
    )

    assert result.status is WecomAppOutboundStatus.NOT_STARTED
    assert result.error_class is WecomAppOutboundErrorClass.INVALID_REQUEST
    token_provider.assert_not_awaited()
    client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection dropped"),
    ],
)
async def test_request_started_transport_failure_is_unknown_without_resubmit(
    transport_error: Exception,
) -> None:
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = transport_error
    sender, _, _ = _sender(client=client)

    first = await sender.send_typed(
        provider_request_id="request-010",
        target="user-001",
        payload=_payload(),
    )
    client.is_closed = True
    readback = await sender.send_typed(
        provider_request_id="request-010",
        target="user-001",
        payload=_payload(),
    )

    assert first.status is WecomAppOutboundStatus.UNKNOWN
    assert first.error_class is WecomAppOutboundErrorClass.TRANSPORT_AMBIGUOUS
    assert readback == first
    client.post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_data",
    [None, [], {}, {"errcode": "0"}, {"errcode": True}],
)
async def test_ambiguous_response_is_unknown(response_data: object) -> None:
    sender, _, client = _sender()
    client.post.return_value = _response(response_data)

    result = await sender.send_typed(
        provider_request_id="request-011",
        target="user-001",
        payload=_payload(),
    )

    assert result.status is WecomAppOutboundStatus.UNKNOWN
    assert result.error_class is WecomAppOutboundErrorClass.RESPONSE_AMBIGUOUS


@pytest.mark.asyncio
async def test_json_parse_failure_is_unknown() -> None:
    sender, _, client = _sender()
    response = MagicMock()
    response.json.side_effect = ValueError("secret response body")
    client.post.return_value = response

    result = await sender.send_typed(
        provider_request_id="request-012",
        target="user-001",
        payload=_payload(content="private payload"),
    )

    assert result.status is WecomAppOutboundStatus.UNKNOWN
    assert "secret response body" not in repr(result)
    assert "private payload" not in repr(sender._requests)


@pytest.mark.asyncio
async def test_nonzero_errcode_is_rejected_without_free_text_leakage() -> None:
    sender, _, client = _sender()
    client.post.return_value = _response({
        "errcode": 40003,
        "errmsg": "secret-token private payload invalid userid",
        "unexpected": {"body": "private payload"},
    })

    result = await sender.send_typed(
        provider_request_id="request-013",
        target="user-001",
        payload=_payload(content="private payload"),
    )
    assert result.status is WecomAppOutboundStatus.REJECTED
    assert result.errcode == 40003
    assert result.error_class is WecomAppOutboundErrorClass.PROVIDER_REJECTED
    evidence = repr(result) + repr(sender._requests)
    assert "errmsg" not in evidence
    assert "secret-token" not in evidence
    assert "private payload" not in evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["invaliduser", "unlicenseduser"])
async def test_errcode_zero_partial_user_failure_is_rejected(field: str) -> None:
    sender, _, client = _sender()
    client.post.return_value = _response({"errcode": 0, field: "private-user"})
    result = await sender.send_typed(
        provider_request_id=f"partial-{field}",
        target="user-001",
        payload=_payload(),
    )
    assert result.status is WecomAppOutboundStatus.REJECTED
    assert result.error_class is WecomAppOutboundErrorClass.PROVIDER_PARTIAL_REJECTED
    assert "private-user" not in repr(result) + repr(sender._requests)


@pytest.mark.asyncio
async def test_unexpected_partial_party_failure_is_unknown() -> None:
    sender, _, client = _sender()
    client.post.return_value = _response({"errcode": 0, "invalidparty": "party"})
    result = await sender.send_typed(
        provider_request_id="partial-party",
        target="user-001",
        payload=_payload(),
    )
    assert result.status is WecomAppOutboundStatus.UNKNOWN


@pytest.mark.asyncio
async def test_unallowlisted_provider_message_id_is_not_returned() -> None:
    sender, _, client = _sender()
    client.post.return_value = _response({
        "errcode": 0,
        "msgid": "unsafe message id with spaces",
    })

    result = await sender.send_typed(
        provider_request_id="request-014",
        target="user-001",
        payload=_payload(),
    )

    assert result.status is WecomAppOutboundStatus.ACKNOWLEDGED
    assert result.provider_message_id is None


@pytest.mark.asyncio
async def test_concurrent_same_identity_shares_one_http_request() -> None:
    request_started = asyncio.Event()
    release_response = asyncio.Event()
    client = AsyncMock()
    client.is_closed = False

    async def post(*args: object, **kwargs: object) -> MagicMock:
        request_started.set()
        await release_response.wait()
        return _response({"errcode": 0})

    client.post.side_effect = post
    sender, token_provider, _ = _sender(client=client)
    calls = [
        asyncio.create_task(sender.send_typed(
            provider_request_id="request-015",
            target="user-001",
            payload=_payload(),
        ))
        for _ in range(20)
    ]
    await request_started.wait()
    release_response.set()
    results = await asyncio.gather(*calls)

    assert all(
        result.status is WecomAppOutboundStatus.ACKNOWLEDGED
        for result in results
    )
    token_provider.assert_awaited_once()
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_identity_conflict_fails_closed() -> None:
    request_started = asyncio.Event()
    release_response = asyncio.Event()
    client = AsyncMock()
    client.is_closed = False

    async def post(*args: object, **kwargs: object) -> MagicMock:
        request_started.set()
        await release_response.wait()
        return _response({"errcode": 0})

    client.post.side_effect = post
    sender, _, _ = _sender(client=client)
    owner = asyncio.create_task(sender.send_typed(
        provider_request_id="request-016",
        target="user-001",
        payload=_payload(content="first"),
    ))
    await request_started.wait()
    conflict = await sender.send_typed(
        provider_request_id="request-016",
        target="user-001",
        payload=_payload(content="different"),
    )
    release_response.set()
    await owner

    assert conflict.status is WecomAppOutboundStatus.NOT_STARTED
    assert conflict.error_class is WecomAppOutboundErrorClass.IDENTITY_CONFLICT
    client.post.assert_awaited_once()
