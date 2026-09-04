"""WebSocket 一次性 ticket 测试。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_issue_ws_ticket_stores_short_lived_payload():
    redis = AsyncMock()
    redis.set.return_value = True

    with patch("services.ws_ticket.get_redis", new=AsyncMock(return_value=redis)):
        from services.ws_ticket import WS_TICKET_TTL_SECONDS, issue_ws_ticket

        ticket = await issue_ws_ticket("user-1", "org-1")

    assert ticket
    redis.set.assert_awaited_once()
    key, value = redis.set.await_args.args[:2]
    assert key.startswith("auth:ws-ticket:")
    assert json.loads(value) == {"user_id": "user-1", "org_id": "org-1"}
    assert redis.set.await_args.kwargs == {
        "ex": WS_TICKET_TTL_SECONDS,
        "nx": True,
    }


@pytest.mark.asyncio
async def test_consume_ws_ticket_is_single_use():
    redis = AsyncMock()
    redis.getdel.return_value = json.dumps({"user_id": "user-1", "org_id": None})

    with patch("services.ws_ticket.get_redis", new=AsyncMock(return_value=redis)):
        from services.ws_ticket import consume_ws_ticket

        result = await consume_ws_ticket("ticket-1")

    assert result == {"user_id": "user-1", "org_id": None}
    redis.getdel.assert_awaited_once_with("auth:ws-ticket:ticket-1")


@pytest.mark.asyncio
async def test_consume_ws_ticket_rejects_missing_or_malformed_ticket():
    redis = AsyncMock()
    redis.getdel.return_value = "not-json"

    with patch("services.ws_ticket.get_redis", new=AsyncMock(return_value=redis)):
        from services.ws_ticket import consume_ws_ticket

        assert await consume_ws_ticket("") is None
        assert await consume_ws_ticket("ticket-1") is None


@pytest.mark.asyncio
async def test_create_ws_ticket_route_uses_authenticated_org_context():
    from api.routes.auth import create_ws_ticket

    with patch(
        "api.routes.auth.issue_ws_ticket",
        new=AsyncMock(return_value="ticket-1"),
    ) as issue:
        result = await create_ws_ticket(
            "user-1",
            SimpleNamespace(org_id="org-1"),
        )

    assert result["ticket"] == "ticket-1"
    issue.assert_awaited_once_with("user-1", "org-1")


@pytest.mark.asyncio
async def test_websocket_endpoint_rejects_consumed_ticket():
    from api.routes.ws import websocket_endpoint

    websocket = type("WebSocketStub", (), {"close": AsyncMock()})()
    with patch(
        "api.routes.ws.consume_ws_ticket",
        new=AsyncMock(return_value=None),
    ):
        await websocket_endpoint(websocket, ticket="already-used")

    websocket.close.assert_awaited_once_with(code=4001, reason="Unauthorized")
