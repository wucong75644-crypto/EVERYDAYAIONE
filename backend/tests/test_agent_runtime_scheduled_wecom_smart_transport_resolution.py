"""Pre-dispatch failure closure for Scheduled Smart transport resolution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from services.agent.runtime.application.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchService,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DeliveryClaim,
    DeliveryClaimKind,
    DeliveryClaimOutcome,
    DeliveryFence,
    DispatchChannel,
    DispatchPayload,
    DispatchPayloadOutcome,
    WecomSmartRobotDispatchTarget,
)
from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    SmartRobotDispatchOutcome,
)


ORG = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _claim() -> DeliveryClaim:
    return DeliveryClaim(
        outcome=DeliveryClaimOutcome.CLAIMED,
        kind=DeliveryClaimKind.INITIAL,
        fence=DeliveryFence(
            intent_id="22222222-2222-2222-2222-222222222222",
            item_id="33333333-3333-3333-3333-333333333333",
            claim_request_id="44444444-4444-4444-4444-444444444444",
            lease_token="55555555-5555-5555-5555-555555555555",
            worker_id="worker", delivery_state_version=3, item_state_version=2,
        ),
        lease_seconds=60, lease_expires_at=NOW, previous_claim_request_id=None,
    )


def _payload() -> DispatchPayload:
    return DispatchPayload(
        outcome=DispatchPayloadOutcome.PAYLOAD, payload_revision=2,
        scheduled_run_id="66666666-6666-6666-6666-666666666666",
        intent_id="22222222-2222-2222-2222-222222222222",
        item_id="33333333-3333-3333-3333-333333333333",
        item_key="a" * 64, ordinal=1, item_kind="text", source_role="text",
        source_revision=1, source_identity_hash="b" * 64,
        content_identity_hash="c" * 64, result_hash="d" * 64,
        target_hash="e" * 64, channel=DispatchChannel.SMART_ROBOT,
        target=WecomSmartRobotDispatchTarget(org_id=ORG, chatid="chat-a"),
        provider_revision=4, delivery_state_version=3, item_state_version=2,
        message_type="text", text="safe text", payload_hash="f" * 64,
    )


class _NoPersistenceRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def prepare_dispatch(self, *_: object) -> object:
        self.calls += 1
        raise AssertionError("unavailable resolution must not prepare")


class _Transport:
    def __init__(self, org_id: str = ORG, is_connected: object = True) -> None:
        self.org_id = org_id
        self.is_connected = is_connected
        self.send_calls = 0

    async def send_proactive_typed(self, *_: object) -> object:
        self.send_calls += 1
        raise AssertionError("unavailable resolution must not send")


class _Resolver:
    def __init__(
        self, result: object | None = None, error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def resolve_smart_transport(self, org_id: str) -> object | None:
        self.calls.append(org_id)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolved,error", [
        (None, None),
        (_Transport("77777777-7777-7777-7777-777777777777"), None),
        (_Transport(is_connected=False), None),
        (_Transport(is_connected=1), None),
        (object(), None),
        (None, RuntimeError("resolver unavailable")),
    ],
)
async def test_unavailable_resolution_has_no_prepare_start_send_or_unknown(
    resolved: object | None, error: BaseException | None,
) -> None:
    repository = _NoPersistenceRepository()
    resolver = _Resolver(resolved, error)

    result = await ScheduledWecomSmartDispatchService(
        repository, resolver,
    ).dispatch_claimed(_claim(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.UNAVAILABLE
    assert result.dispatch_receipt is None
    assert resolver.calls == [ORG]
    assert repository.calls == 0
    if isinstance(resolved, _Transport):
        assert resolved.send_calls == 0


@pytest.mark.asyncio
async def test_resolver_cancellation_propagates_without_persistence() -> None:
    repository = _NoPersistenceRepository()
    resolver = _Resolver(error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await ScheduledWecomSmartDispatchService(
            repository, resolver,
        ).dispatch_claimed(_claim(), _payload())

    assert resolver.calls == [ORG]
    assert repository.calls == 0
