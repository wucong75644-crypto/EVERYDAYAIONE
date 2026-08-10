"""Regression tests for strict partial receipts and absolute deadlines."""

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom.app_outbound import (
    WecomAppOutbound,
    WecomAppOutboundErrorClass,
    WecomAppOutboundStatus,
)


DEADLINE = 0.01
LATE_DELAY = 0.25
DEADLINE_TOLERANCE = 0.12


def _payload() -> dict[str, Any]:
    return {
        "touser": "user-001",
        "msgtype": "text",
        "agentid": 1000,
        "text": {"content": "safe"},
    }


def _response(data: object) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = data
    return response


MALFORMED_PARTIAL_FIELDS = [
    pytest.param("invaliduser", [], id="invaliduser-list"),
    pytest.param("invaliduser", True, id="invaliduser-bool"),
    pytest.param("invaliduser", {}, id="invaliduser-dict"),
    pytest.param("invaliduser", 7, id="invaliduser-int"),
    pytest.param("invaliduser", None, id="invaliduser-none"),
    pytest.param("unlicenseduser", ["user"], id="unlicenseduser-list"),
    pytest.param("unlicenseduser", False, id="unlicenseduser-bool"),
    pytest.param("unlicenseduser", {"user": 1}, id="unlicenseduser-dict"),
    pytest.param("invalidparty", [], id="invalidparty-list"),
    pytest.param("invalidparty", True, id="invalidparty-bool"),
    pytest.param("invalidparty", 9, id="invalidparty-int"),
    pytest.param("invalidtag", {}, id="invalidtag-dict"),
    pytest.param("invalidtag", None, id="invalidtag-none"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "value"), MALFORMED_PARTIAL_FIELDS)
async def test_malformed_partial_field_is_unknown(
    field: str,
    value: object,
) -> None:
    token_provider = AsyncMock(return_value="token")
    client = AsyncMock()
    client.is_closed = False
    client.post.return_value = _response({"errcode": 0, field: value})
    sender = WecomAppOutbound(
        token_provider=token_provider,
        http_client=client,
    )

    result = await sender.send_typed(
        provider_request_id=f"malformed-{field}",
        target="user-001",
        payload=_payload(),
    )

    assert result.status is WecomAppOutboundStatus.UNKNOWN
    assert result.error_class is WecomAppOutboundErrorClass.RESPONSE_AMBIGUOUS


@pytest.mark.asyncio
async def test_credential_deadline_returns_before_cancel_suppressor_finishes() -> None:
    entered = asyncio.Event()

    async def late_token() -> str:
        entered.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(LATE_DELAY)
            return "late-token"

    client = AsyncMock()
    client.is_closed = False
    client.post.return_value = _response({"errcode": 0})
    sender = WecomAppOutbound(
        token_provider=late_token,
        http_client=client,
        credential_timeout=DEADLINE,
    )
    started = asyncio.get_running_loop().time()
    result = await sender.send_typed(
        provider_request_id="late-credential",
        target="user-001",
        payload=_payload(),
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < DEADLINE_TOLERANCE
    assert result.status is WecomAppOutboundStatus.NOT_STARTED
    assert result.error_class is WecomAppOutboundErrorClass.CREDENTIAL_TIMEOUT
    await asyncio.sleep(LATE_DELAY + 0.02)
    client.post.assert_not_awaited()
    assert result.status is WecomAppOutboundStatus.NOT_STARTED

    entered.clear()
    cancelled = asyncio.create_task(sender.send_typed(
        provider_request_id="cancelled-credential",
        target="user-001",
        payload=_payload(),
    ))
    await entered.wait()
    cancelled.cancel()
    cancel_started = asyncio.get_running_loop().time()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert asyncio.get_running_loop().time() - cancel_started < DEADLINE_TOLERANCE
    await asyncio.sleep(LATE_DELAY + 0.02)
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_deadline_keeps_unknown_after_late_ack() -> None:
    async def late_post(*args: object, **kwargs: object) -> MagicMock:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(LATE_DELAY)
            return _response({"errcode": 0, "msgid": "late-msg"})

    token_provider = AsyncMock(return_value="token")
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = late_post
    sender = WecomAppOutbound(
        token_provider=token_provider,
        http_client=client,
        post_timeout=DEADLINE,
    )
    started = asyncio.get_running_loop().time()
    result = await sender.send_typed(
        provider_request_id="late-post-request",
        target="user-001",
        payload=_payload(),
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < DEADLINE_TOLERANCE
    assert result.status is WecomAppOutboundStatus.UNKNOWN
    assert result.error_class is WecomAppOutboundErrorClass.POST_TIMEOUT
    await asyncio.sleep(LATE_DELAY + 0.02)
    readback = await sender.send_typed(
        provider_request_id="late-post-request",
        target="user-001",
        payload=_payload(),
    )
    assert readback == result
    token_provider.assert_awaited_once()
    client.post.assert_awaited_once()
