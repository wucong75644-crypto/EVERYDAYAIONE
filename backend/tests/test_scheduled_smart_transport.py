"""Tenant-boundary coverage for the Scheduled Smart Robot resolver."""

from __future__ import annotations

import pytest

from services.wecom.scheduled_smart_transport import (
    ScheduledSmartTransportResolver,
)


ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"


class _Client:
    def __init__(
        self, org_id: str, *, is_connected: object = True,
        bot_secret: str = "bot-secret-must-not-leak",
    ) -> None:
        self.org_id = org_id
        self.is_connected = is_connected
        self.bot_secret = bot_secret

    async def send_proactive_typed(self, *_: object) -> object:
        return object()

    def lookup_outbound_result(self, *_: object) -> object:
        return object()


@pytest.mark.asyncio
async def test_resolves_two_tenants_to_their_exact_connected_clients() -> None:
    clients = {ORG_A: _Client(ORG_A), ORG_B: _Client(ORG_B)}
    calls: list[str] = []

    def get_client(org_id: str) -> object | None:
        calls.append(org_id)
        return clients.get(org_id)

    resolver = ScheduledSmartTransportResolver(get_client)

    assert await resolver.resolve_smart_transport(ORG_A) is clients[ORG_A]
    assert await resolver.resolve_smart_transport(ORG_B) is clients[ORG_B]
    assert calls == [ORG_A, ORG_B]
    assert "bot-secret-must-not-leak" not in repr(resolver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client", [
        None,
        _Client(ORG_B),
        _Client(ORG_A, is_connected=False),
        _Client(ORG_A, is_connected=1),
        object(),
    ],
)
async def test_missing_mismatched_disconnected_or_incomplete_client_returns_none(
    client: object | None,
) -> None:
    resolver = ScheduledSmartTransportResolver(lambda _: client)

    assert await resolver.resolve_smart_transport(ORG_A) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "org_id", [
        "11111111-1111-1111-1111-11111111111A",
        "{11111111-1111-1111-1111-111111111111}",
        "11111111111111111111111111111111",
        "not-an-org",
        "",
    ],
)
async def test_noncanonical_org_is_rejected_before_getter(org_id: str) -> None:
    calls: list[str] = []
    resolver = ScheduledSmartTransportResolver(
        lambda value: calls.append(value) or _Client(ORG_A),
    )

    assert await resolver.resolve_smart_transport(org_id) is None
    assert await resolver.resolve_smart_readback(org_id) is None
    assert calls == []


@pytest.mark.asyncio
async def test_readback_resolves_exact_disconnected_client_with_lookup() -> None:
    client = _Client(ORG_A, is_connected=False)
    resolver = ScheduledSmartTransportResolver(lambda _: client)

    assert await resolver.resolve_smart_readback(ORG_A) is client
    assert await resolver.resolve_smart_transport(ORG_A) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [
    _Client(ORG_B, is_connected=False),
    object(),
])
async def test_readback_rejects_cross_org_or_missing_lookup(
    client: object,
) -> None:
    resolver = ScheduledSmartTransportResolver(lambda _: client)

    assert await resolver.resolve_smart_readback(ORG_A) is None
