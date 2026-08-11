"""Unified Scheduled WeCom router behavior and safety coverage."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from services.agent.runtime.application.scheduled_wecom_router import (
    ScheduledWecomRouter,
)
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import (
    AppDispatchOutcome,
    AppDispatchResult,
    ScheduledWecomAppBinding,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DeliveryClaim,
    DeliveryClaimKind,
    DeliveryClaimOutcome,
    DeliveryFence,
    DeliveryStatus,
    DispatchChannel,
    DispatchPayload,
    DispatchPayloadOutcome,
    ItemStatus,
    UnsupportedDispatchPayload,
    UnsupportedReason,
    UnsupportedTerminalizationOutcome,
    UnsupportedTerminalizationReceipt,
    UnavailableDispatchPayload,
    UnavailableReason,
    WecomAppDispatchTarget,
    WecomSmartRobotDispatchTarget,
)
from services.agent.runtime.ports.scheduled_wecom_router import (
    ScheduledWecomRouteOutcome,
)
from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    SmartRobotDispatchOutcome,
    SmartRobotDispatchResult,
)


INTENT = "11111111-1111-1111-1111-111111111111"
ITEM = "22222222-2222-2222-2222-222222222222"
CLAIM_REQUEST = "33333333-3333-3333-3333-333333333333"
LEASE = "44444444-4444-4444-4444-444444444444"
ORG = "55555555-5555-5555-5555-555555555555"
RUN = "66666666-6666-6666-6666-666666666666"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _claim(outcome: DeliveryClaimOutcome = DeliveryClaimOutcome.CLAIMED) -> DeliveryClaim:
    return DeliveryClaim(
        outcome=outcome,
        kind=DeliveryClaimKind.INITIAL,
        fence=DeliveryFence(
            intent_id=INTENT,
            item_id=ITEM,
            claim_request_id=CLAIM_REQUEST,
            lease_token=LEASE,
            worker_id="router-worker",
            delivery_state_version=3,
            item_state_version=2,
        ),
        lease_seconds=60,
        lease_expires_at=NOW,
        previous_claim_request_id=None,
    )


def _payload(channel: DispatchChannel) -> DispatchPayload:
    target = (
        WecomAppDispatchTarget(
            org_id=ORG, corp_id="corp-tenant-a", wecom_userid="member-a",
        )
        if channel is DispatchChannel.APP
        else WecomSmartRobotDispatchTarget(org_id=ORG, chatid="chat-a")
    )
    return DispatchPayload(
        outcome=DispatchPayloadOutcome.PAYLOAD,
        payload_revision=2,
        scheduled_run_id=RUN,
        intent_id=INTENT,
        item_id=ITEM,
        item_key="a" * 64,
        ordinal=1,
        item_kind="text",
        source_role="text",
        source_revision=1,
        source_identity_hash="b" * 64,
        content_identity_hash="c" * 64,
        result_hash="d" * 64,
        target_hash="e" * 64,
        channel=channel,
        target=target,
        provider_revision=4,
        delivery_state_version=3,
        item_state_version=2,
        message_type="text",
        text="任务完成",
        payload_hash="f" * 64,
    )


class _Repository:
    def __init__(self, claim: DeliveryClaim | None, payload: object) -> None:
        self.claim = claim
        self.payload = payload
        self.claim_calls = 0
        self.read_calls = 0
        self.terminalize_ids: list[str] = []
        self.claim_error: Exception | None = None
        self.read_error: Exception | None = None

    async def claim_delivery(self, **_: object) -> DeliveryClaim | None:
        self.claim_calls += 1
        if self.claim_error is not None:
            raise self.claim_error
        return self.claim

    async def read_dispatch_payload(self, claim: DeliveryClaim) -> object:
        assert claim is self.claim
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.payload

    async def terminalize_unsupported(
        self, claim: DeliveryClaim, *, request_id: str,
    ) -> UnsupportedTerminalizationReceipt:
        assert isinstance(self.payload, UnsupportedDispatchPayload)
        self.terminalize_ids.append(request_id)
        return UnsupportedTerminalizationReceipt(
            outcome=(
                UnsupportedTerminalizationOutcome.TERMINALIZED
                if len(self.terminalize_ids) == 1
                else UnsupportedTerminalizationOutcome.READBACK
            ),
            request_id=request_id,
            intent_id=claim.fence.intent_id,
            item_id=claim.fence.item_id,
            reason=self.payload.reason,
            item_status=ItemStatus.CANCELLED,
            delivery_status=DeliveryStatus.FAILED,
            delivery_state_version=4,
            item_state_version=3,
            terminalized_at=NOW,
        )


class _SmartDispatch:
    def __init__(
        self, outcome: SmartRobotDispatchOutcome = SmartRobotDispatchOutcome.ACCEPTED,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.outcome = outcome
        self.gate = gate
        self.calls = 0

    async def dispatch_claimed(
        self, claim: DeliveryClaim, payload: DispatchPayload,
    ) -> SmartRobotDispatchResult:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return SmartRobotDispatchResult(
            outcome=self.outcome,
            intent_id=claim.fence.intent_id,
            item_id=payload.item_id,
        )


class _AppDispatch:
    def __init__(
        self, outcome: AppDispatchOutcome = AppDispatchOutcome.ACCEPTED,
    ) -> None:
        self.outcome = outcome
        self.calls = 0
        self.binding: ScheduledWecomAppBinding | None = None

    async def dispatch_claimed(
        self,
        claim: DeliveryClaim,
        payload: DispatchPayload,
        binding: ScheduledWecomAppBinding,
    ) -> AppDispatchResult:
        self.calls += 1
        self.binding = binding
        return AppDispatchResult(
            outcome=self.outcome,
            intent_id=claim.fence.intent_id,
            item_id=payload.item_id,
        )


class _Transport:
    def __init__(self) -> None:
        self.credential = "must-not-leak"

    async def send_typed(self, **_: object) -> object:
        raise AssertionError("router must not call transport directly")


class _Resolver:
    def __init__(self, binding: ScheduledWecomAppBinding | None) -> None:
        self.binding = binding
        self.calls: list[tuple[str, str]] = []
        self.error: Exception | None = None

    async def resolve_app_binding(
        self, *, org_id: str, corp_id: str,
    ) -> ScheduledWecomAppBinding | None:
        self.calls.append((org_id, corp_id))
        if self.error is not None:
            raise self.error
        return self.binding


def _router(
    repository: _Repository,
    *,
    smart: _SmartDispatch | None = None,
    app: _AppDispatch | None = None,
    resolver: _Resolver | None = None,
) -> tuple[ScheduledWecomRouter, _SmartDispatch, _AppDispatch, _Resolver]:
    smart = smart or _SmartDispatch()
    app = app or _AppDispatch()
    resolver = resolver or _Resolver(None)
    return ScheduledWecomRouter(repository, smart, app, resolver), smart, app, resolver


@pytest.mark.asyncio
async def test_empty_unavailable_and_response_loss_have_no_dispatch() -> None:
    cases = [
        (None, None, ScheduledWecomRouteOutcome.EMPTY),
        (_claim(DeliveryClaimOutcome.FENCED), None, ScheduledWecomRouteOutcome.UNAVAILABLE),
        (_claim(), None, ScheduledWecomRouteOutcome.UNAVAILABLE),
        (
            _claim(),
            UnavailableDispatchPayload(
                outcome=DispatchPayloadOutcome.UNAVAILABLE,
                reason=UnavailableReason.TARGET,
            ),
            ScheduledWecomRouteOutcome.UNAVAILABLE,
        ),
    ]
    for claim, payload, expected in cases:
        repository = _Repository(claim, payload)
        router, smart, app, resolver = _router(repository)
        result = await router.dispatch_once(request_id=CLAIM_REQUEST, worker_id="worker")
        assert result.outcome is expected
        assert smart.calls == app.calls == 0
        assert resolver.calls == []

    repository = _Repository(_claim(), _payload(DispatchChannel.SMART_ROBOT))
    repository.claim_error = ConnectionError("claim response lost")
    result = await _router(repository)[0].dispatch_once(
        request_id=CLAIM_REQUEST, worker_id="worker",
    )
    assert result.outcome is ScheduledWecomRouteOutcome.UNAVAILABLE

    repository = _Repository(
        _claim(DeliveryClaimOutcome.READBACK),
        _payload(DispatchChannel.SMART_ROBOT),
    )
    router, smart, _, _ = _router(repository)
    result = await router.dispatch_once(
        request_id=CLAIM_REQUEST, worker_id="router-worker",
    )
    assert result.outcome is ScheduledWecomRouteOutcome.ACCEPTED
    assert smart.calls == 1
    repository.claim_error = None
    repository.read_error = ConnectionError("read response lost")
    result = await _router(repository)[0].dispatch_once(
        request_id=CLAIM_REQUEST, worker_id="worker",
    )
    assert result.outcome is ScheduledWecomRouteOutcome.UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", list(UnsupportedReason))
async def test_all_unsupported_reasons_terminalize_with_stable_replay_id(reason) -> None:
    payload = UnsupportedDispatchPayload(
        outcome=DispatchPayloadOutcome.UNSUPPORTED,
        reason=reason,
    )
    repository = _Repository(_claim(), payload)
    router, smart, app, resolver = _router(repository)
    first = await router.dispatch_once(request_id=CLAIM_REQUEST, worker_id="worker")
    second = await router.dispatch_once(request_id=CLAIM_REQUEST, worker_id="worker")
    assert first.outcome is second.outcome is ScheduledWecomRouteOutcome.UNSUPPORTED
    assert first.unsupported_reason is second.unsupported_reason is reason
    assert len(repository.terminalize_ids) == 2
    assert repository.terminalize_ids[0] == repository.terminalize_ids[1]
    assert smart.calls == app.calls == 0
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_outcome", "route_outcome"),
    [
        (SmartRobotDispatchOutcome.ACCEPTED, ScheduledWecomRouteOutcome.ACCEPTED),
        (SmartRobotDispatchOutcome.REJECTED, ScheduledWecomRouteOutcome.REJECTED),
        (SmartRobotDispatchOutcome.UNAVAILABLE, ScheduledWecomRouteOutcome.UNAVAILABLE),
        (SmartRobotDispatchOutcome.UNKNOWN, ScheduledWecomRouteOutcome.UNKNOWN),
        (
            SmartRobotDispatchOutcome.ALREADY_PERSISTED,
            ScheduledWecomRouteOutcome.ALREADY_PERSISTED,
        ),
    ],
)
async def test_smart_robot_routes_typed_outcomes(service_outcome, route_outcome) -> None:
    repository = _Repository(_claim(), _payload(DispatchChannel.SMART_ROBOT))
    smart = _SmartDispatch(service_outcome)
    router, _, app, resolver = _router(repository, smart=smart)
    result = await router.dispatch_once(request_id=CLAIM_REQUEST, worker_id="worker")
    assert result.outcome is route_outcome
    assert smart.calls == 1
    assert app.calls == 0
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_outcome", "route_outcome"),
    [
        (AppDispatchOutcome.ACCEPTED, ScheduledWecomRouteOutcome.ACCEPTED),
        (AppDispatchOutcome.REJECTED, ScheduledWecomRouteOutcome.REJECTED),
        (AppDispatchOutcome.UNKNOWN, ScheduledWecomRouteOutcome.UNKNOWN),
        (AppDispatchOutcome.ALREADY_PERSISTED, ScheduledWecomRouteOutcome.ALREADY_PERSISTED),
    ],
)
async def test_app_resolves_exact_tenant_binding_and_routes(
    service_outcome, route_outcome,
) -> None:
    repository = _Repository(_claim(), _payload(DispatchChannel.APP))
    binding = ScheduledWecomAppBinding(
        org_id=ORG, corp_id="corp-tenant-a", agent_id=1001, transport=_Transport(),
    )
    app = _AppDispatch(service_outcome)
    resolver = _Resolver(binding)
    router, smart, _, _ = _router(repository, app=app, resolver=resolver)
    result = await router.dispatch_once(request_id=CLAIM_REQUEST, worker_id="worker")
    assert result.outcome is route_outcome
    assert resolver.calls == [(ORG, "corp-tenant-a")]
    assert app.calls == 1 and app.binding is binding
    assert smart.calls == 0
    assert "must-not-leak" not in repr(result)
    assert "must-not-leak" not in repr(binding)


@pytest.mark.asyncio
@pytest.mark.parametrize("binding_kind", ["missing", "org", "corp", "agent", "transport"])
async def test_app_missing_incomplete_or_mismatched_binding_fails_closed(
    binding_kind: str,
) -> None:
    transport = _Transport()
    binding = ScheduledWecomAppBinding(
        org_id=ORG,
        corp_id="corp-tenant-a",
        agent_id=1001,
        transport=transport,
    )
    if binding_kind == "missing":
        resolved = None
    elif binding_kind == "org":
        resolved = replace(binding, org_id="other-org")
    elif binding_kind == "corp":
        resolved = replace(binding, corp_id="other-corp")
    elif binding_kind == "agent":
        resolved = replace(binding, agent_id=0)
    else:
        resolved = replace(binding, transport=object())
    repository = _Repository(_claim(), _payload(DispatchChannel.APP))
    router, smart, app, _ = _router(repository, resolver=_Resolver(resolved))
    result = await router.dispatch_once(request_id=CLAIM_REQUEST, worker_id="worker")
    assert result.outcome is ScheduledWecomRouteOutcome.CONFIG_UNAVAILABLE
    assert smart.calls == app.calls == 0


@pytest.mark.asyncio
async def test_route_fence_drift_stops_before_resolver_or_dispatch() -> None:
    payload = replace(_payload(DispatchChannel.APP), item_state_version=99)
    repository = _Repository(_claim(), payload)
    resolver = _Resolver(ScheduledWecomAppBinding(
        org_id=ORG, corp_id="corp-tenant-a", agent_id=1001, transport=_Transport(),
    ))
    router, smart, app, _ = _router(repository, resolver=resolver)
    result = await router.dispatch_once(request_id=CLAIM_REQUEST, worker_id="worker")
    assert result.outcome is ScheduledWecomRouteOutcome.UNAVAILABLE
    assert resolver.calls == []
    assert smart.calls == app.calls == 0


@pytest.mark.asyncio
async def test_fifty_same_request_calls_claim_read_and_dispatch_once() -> None:
    gate = asyncio.Event()
    repository = _Repository(_claim(), _payload(DispatchChannel.SMART_ROBOT))
    smart = _SmartDispatch(gate=gate)
    router = _router(repository, smart=smart)[0]
    calls = [asyncio.create_task(router.dispatch_once(
        request_id=CLAIM_REQUEST, worker_id="worker",
    )) for _ in range(50)]
    while smart.calls == 0:
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*calls)
    assert {result.outcome for result in results} == {ScheduledWecomRouteOutcome.ACCEPTED}
    assert repository.claim_calls == repository.read_calls == smart.calls == 1


@pytest.mark.asyncio
async def test_same_inflight_request_with_different_owner_fails_closed() -> None:
    gate = asyncio.Event()
    repository = _Repository(_claim(), _payload(DispatchChannel.SMART_ROBOT))
    smart = _SmartDispatch(gate=gate)
    router = _router(repository, smart=smart)[0]
    owner = asyncio.create_task(router.dispatch_once(
        request_id=CLAIM_REQUEST, worker_id="worker-a",
    ))
    while smart.calls == 0:
        await asyncio.sleep(0)
    conflict = await router.dispatch_once(
        request_id=CLAIM_REQUEST, worker_id="worker-b",
    )
    gate.set()
    assert (await owner).outcome is ScheduledWecomRouteOutcome.ACCEPTED
    assert conflict.outcome is ScheduledWecomRouteOutcome.UNAVAILABLE
    assert repository.claim_calls == repository.read_calls == smart.calls == 1
