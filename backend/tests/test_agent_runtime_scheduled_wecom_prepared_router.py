"""Prepared-recovery routing for Runtime-owned Scheduled WeCom delivery."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_app_identity,
    scheduled_wecom_smart_identity,
)
from services.agent.runtime.application.scheduled_wecom_router import (
    ScheduledWecomRouter,
)
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import (
    AppDispatchOutcome,
    AppDispatchResult,
    ScheduledWecomAppBinding,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DeliveryClaim,
    DeliveryClaimKind,
    DeliveryClaimOutcome,
    DeliveryFence,
    DispatchAttempt,
    DispatchChannel,
    DispatchPayload,
    DispatchPayloadOutcome,
    DispatchPayloadVersions,
    PreparedRecovery,
    RecoveryOutcome,
    UnsupportedDispatchPayload,
    UnsupportedReason,
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
REQUEST = "33333333-3333-3333-3333-333333333333"
LEASE = "44444444-4444-4444-4444-444444444444"
ATTEMPT = "55555555-5555-5555-5555-555555555555"
ORG = "66666666-6666-6666-6666-666666666666"
RUN = "77777777-7777-7777-7777-777777777777"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


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


def _recovery(
    payload: DispatchPayload,
    outcome: RecoveryOutcome = RecoveryOutcome.RECOVERED,
    *,
    app_binding: ScheduledWecomAppBinding | None = None,
) -> PreparedRecovery:
    identity = (
        scheduled_wecom_app_identity(
            payload,
            org_id=app_binding.org_id,
            corp_id=app_binding.corp_id,
            agent_id=app_binding.agent_id,
        )
        if app_binding is not None
        else scheduled_wecom_smart_identity(payload)
    )
    return PreparedRecovery(
        outcome=outcome,
        attempt=DispatchAttempt(
            outcome=AttemptOperationOutcome.READBACK,
            fence=DeliveryFence(
                intent_id=INTENT,
                item_id=ITEM,
                claim_request_id=REQUEST,
                lease_token=LEASE,
                worker_id="recovery-worker",
                delivery_state_version=8,
                item_state_version=7,
            ),
            attempt_id=ATTEMPT,
            attempt_number=1,
            identity=identity,
            payload_versions=DispatchPayloadVersions(
                delivery_state_version=3,
                item_state_version=2,
            ),
            status=AttemptStatus.PREPARED,
        ),
        lease_expires_at=NOW,
    )


class _Repository:
    def __init__(
        self, recovery: PreparedRecovery | None, payload: object,
    ) -> None:
        self.recovery = recovery
        self.payload = payload
        self.recover_calls = 0
        self.read_calls = 0
        self.claim_calls = 0
        self.normal_read_calls = 0
        self.recover_error: Exception | None = None
        self.read_error: Exception | None = None

    async def recover_prepared(self, **_: object) -> PreparedRecovery | None:
        self.recover_calls += 1
        if self.recover_error is not None:
            raise self.recover_error
        return self.recovery

    async def read_prepared_dispatch_payload(
        self, recovery: PreparedRecovery,
    ) -> object:
        assert recovery is self.recovery
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.payload

    async def claim_delivery(self, **_: object) -> DeliveryClaim:
        self.claim_calls += 1
        return DeliveryClaim(
            outcome=DeliveryClaimOutcome.CLAIMED,
            kind=DeliveryClaimKind.INITIAL,
            fence=DeliveryFence(
                intent_id=INTENT,
                item_id=ITEM,
                claim_request_id=REQUEST,
                lease_token=LEASE,
                worker_id="normal-worker",
                delivery_state_version=3,
                item_state_version=2,
            ),
            lease_seconds=60,
            lease_expires_at=NOW,
            previous_claim_request_id=None,
        )

    async def read_dispatch_payload(self, _: DeliveryClaim) -> object:
        self.normal_read_calls += 1
        return self.payload

    async def terminalize_unsupported(self, *_: object, **__: object) -> object:
        raise AssertionError("prepared recovery must not terminalize")


class _SmartDispatch:
    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.normal_calls = 0
        self.recovery_calls: list[tuple[PreparedRecovery, DispatchPayload]] = []
        self.error: Exception | None = None

    async def dispatch_claimed(
        self, claim: DeliveryClaim, payload: DispatchPayload,
    ) -> SmartRobotDispatchResult:
        self.normal_calls += 1
        return SmartRobotDispatchResult(
            outcome=SmartRobotDispatchOutcome.ACCEPTED,
            intent_id=claim.fence.intent_id,
            item_id=payload.item_id,
        )

    async def dispatch_recovered_prepared(
        self, recovery: PreparedRecovery, payload: DispatchPayload,
    ) -> SmartRobotDispatchResult:
        self.recovery_calls.append((recovery, payload))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return SmartRobotDispatchResult(
            outcome=SmartRobotDispatchOutcome.ACCEPTED,
            intent_id=payload.intent_id,
            item_id=payload.item_id,
        )


class _AppDispatch:
    def __init__(self) -> None:
        self.normal_calls = 0
        self.recovery_calls: list[
            tuple[PreparedRecovery, DispatchPayload, ScheduledWecomAppBinding]
        ] = []

    async def dispatch_claimed(
        self, claim: DeliveryClaim, payload: DispatchPayload,
        binding: ScheduledWecomAppBinding,
    ) -> AppDispatchResult:
        self.normal_calls += 1
        return AppDispatchResult(
            outcome=AppDispatchOutcome.ACCEPTED,
            intent_id=claim.fence.intent_id,
            item_id=payload.item_id,
        )

    async def dispatch_recovered_prepared(
        self, recovery: PreparedRecovery, payload: DispatchPayload,
        binding: ScheduledWecomAppBinding,
    ) -> AppDispatchResult:
        self.recovery_calls.append((recovery, payload, binding))
        return AppDispatchResult(
            outcome=AppDispatchOutcome.ACCEPTED,
            intent_id=payload.intent_id,
            item_id=payload.item_id,
        )


class _Transport:
    async def send_typed(self, **_: object) -> object:
        raise AssertionError("router must not send directly")


class _Resolver:
    def __init__(self, binding: ScheduledWecomAppBinding | None = None) -> None:
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
    resolver = resolver or _Resolver()
    return ScheduledWecomRouter(repository, smart, app, resolver), smart, app, resolver


@pytest.mark.asyncio
async def test_prepared_smart_routes_original_attempt_without_normal_dispatch() -> None:
    payload = _payload(DispatchChannel.SMART_ROBOT)
    recovery = _recovery(payload)
    repository = _Repository(recovery, payload)
    router, smart, app, resolver = _router(repository)
    result = await router.recover_prepared_once(
        request_id=REQUEST, worker_id="recovery-worker",
    )
    assert result.outcome is ScheduledWecomRouteOutcome.ACCEPTED
    assert smart.recovery_calls == [(recovery, payload)]
    assert smart.normal_calls == app.normal_calls == 0
    assert app.recovery_calls == resolver.calls == []
    assert repository.claim_calls == repository.normal_read_calls == 0
    assert repository.recover_calls == repository.read_calls == 1


@pytest.mark.asyncio
async def test_prepared_app_resolves_exact_binding_and_routes_original_attempt() -> None:
    payload = _payload(DispatchChannel.APP)
    binding = ScheduledWecomAppBinding(
        org_id=ORG, corp_id="corp-tenant-a", agent_id=1001,
        transport=_Transport(),
    )
    recovery = _recovery(payload, app_binding=binding)
    repository = _Repository(recovery, payload)
    router, smart, app, resolver = _router(
        repository, resolver=_Resolver(binding),
    )
    result = await router.recover_prepared_once(
        request_id=REQUEST, worker_id="recovery-worker",
    )
    assert result.outcome is ScheduledWecomRouteOutcome.ACCEPTED
    assert resolver.calls == [(ORG, "corp-tenant-a")]
    assert app.recovery_calls == [(recovery, payload, binding)]
    assert app.normal_calls == smart.normal_calls == 0
    assert smart.recovery_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ("empty", "fenced", "read_error", "missing", "unsupported", "unavailable",
     "version_drift", "target_drift", "identity_drift"),
)
async def test_prepared_unreadable_or_drifted_input_has_no_dispatch(
    case: str,
) -> None:
    payload: object = _payload(DispatchChannel.SMART_ROBOT)
    recovery = _recovery(payload)
    if case == "empty":
        recovery = None
    elif case == "fenced":
        recovery = replace(recovery, outcome=RecoveryOutcome.FENCED)
    elif case == "missing":
        payload = None
    elif case == "unsupported":
        payload = UnsupportedDispatchPayload(
            outcome=DispatchPayloadOutcome.UNSUPPORTED,
            reason=UnsupportedReason.NON_COMPLETED_CONTENT,
        )
    elif case == "unavailable":
        payload = UnavailableDispatchPayload(
            outcome=DispatchPayloadOutcome.UNAVAILABLE,
            reason=UnavailableReason.TARGET,
        )
    elif case == "version_drift":
        payload = replace(payload, item_state_version=99)
    elif case == "target_drift":
        payload = replace(
            payload,
            target=WecomAppDispatchTarget(
                org_id=ORG, corp_id="corp-tenant-a", wecom_userid="member-a",
            ),
        )
    elif case == "identity_drift":
        recovery = replace(
            recovery,
            attempt=replace(
                recovery.attempt,
                identity=replace(
                    recovery.attempt.identity,
                    idempotency_key="0" * 64,
                ),
            ),
        )
    repository = _Repository(recovery, payload)
    if case == "read_error":
        repository.read_error = ConnectionError("response lost")
    router, smart, app, resolver = _router(repository)
    result = await router.recover_prepared_once(
        request_id=REQUEST, worker_id="recovery-worker",
    )
    expected = (
        ScheduledWecomRouteOutcome.EMPTY
        if case == "empty"
        else ScheduledWecomRouteOutcome.UNAVAILABLE
    )
    assert result.outcome is expected
    assert smart.recovery_calls == app.recovery_calls == []
    assert smart.normal_calls == app.normal_calls == 0
    assert resolver.calls == []
    assert repository.claim_calls == repository.normal_read_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver_failure", ["missing", "error", "mismatch"])
async def test_prepared_app_binding_failure_is_config_unavailable(
    resolver_failure: str,
) -> None:
    payload = _payload(DispatchChannel.APP)
    expected_binding = ScheduledWecomAppBinding(
        org_id=ORG, corp_id="corp-tenant-a", agent_id=1001,
        transport=_Transport(),
    )
    recovery = _recovery(payload, app_binding=expected_binding)
    resolved = (
        replace(expected_binding, org_id="other-org")
        if resolver_failure == "mismatch"
        else None
    )
    resolver = _Resolver(resolved)
    if resolver_failure == "error":
        resolver.error = ConnectionError("config unavailable")
    router, smart, app, _ = _router(
        _Repository(recovery, payload), resolver=resolver,
    )
    result = await router.recover_prepared_once(
        request_id=REQUEST, worker_id="recovery-worker",
    )
    assert result.outcome is ScheduledWecomRouteOutcome.CONFIG_UNAVAILABLE
    assert smart.recovery_calls == app.recovery_calls == []


@pytest.mark.asyncio
async def test_fifty_prepared_recovery_waiters_share_one_flight() -> None:
    gate = asyncio.Event()
    payload = _payload(DispatchChannel.SMART_ROBOT)
    recovery = _recovery(payload)
    repository = _Repository(recovery, payload)
    smart = _SmartDispatch(gate)
    router = _router(repository, smart=smart)[0]
    tasks = [asyncio.create_task(router.recover_prepared_once(
        request_id=REQUEST, worker_id="recovery-worker",
    )) for _ in range(50)]
    while not smart.recovery_calls:
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert {item.outcome for item in results} == {
        ScheduledWecomRouteOutcome.ACCEPTED,
    }
    assert repository.recover_calls == repository.read_calls == 1
    assert len(smart.recovery_calls) == 1


@pytest.mark.asyncio
async def test_prepared_recovery_conflict_and_waiter_cancellation_fail_closed() -> None:
    gate = asyncio.Event()
    payload = _payload(DispatchChannel.SMART_ROBOT)
    repository = _Repository(_recovery(payload), payload)
    smart = _SmartDispatch(gate)
    router = _router(repository, smart=smart)[0]
    owner = asyncio.create_task(router.recover_prepared_once(
        request_id=REQUEST, worker_id="recovery-worker", lease_seconds=60,
    ))
    while not smart.recovery_calls:
        await asyncio.sleep(0)
    conflict = await router.recover_prepared_once(
        request_id=REQUEST, worker_id="other-worker", lease_seconds=61,
    )
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    survivor = asyncio.create_task(router.recover_prepared_once(
        request_id=REQUEST, worker_id="recovery-worker", lease_seconds=60,
    ))
    gate.set()
    assert conflict.outcome is ScheduledWecomRouteOutcome.UNAVAILABLE
    assert (await survivor).outcome is ScheduledWecomRouteOutcome.ACCEPTED
    assert repository.recover_calls == repository.read_calls == 1
    assert len(smart.recovery_calls) == 1


@pytest.mark.asyncio
async def test_recovered_dispatch_exception_propagates_without_router_retry() -> None:
    payload = _payload(DispatchChannel.SMART_ROBOT)
    repository = _Repository(_recovery(payload), payload)
    smart = _SmartDispatch()
    smart.error = RuntimeError("post-start persistence failed")
    router = _router(repository, smart=smart)[0]
    with pytest.raises(RuntimeError, match="post-start persistence failed"):
        await router.recover_prepared_once(
            request_id=REQUEST, worker_id="recovery-worker",
        )
    assert repository.recover_calls == repository.read_calls == 1
    assert len(smart.recovery_calls) == 1


@pytest.mark.asyncio
async def test_normal_dispatch_flight_remains_separate_and_unchanged() -> None:
    payload = _payload(DispatchChannel.SMART_ROBOT)
    repository = _Repository(_recovery(payload), payload)
    router, smart, app, _ = _router(repository)
    result = await router.dispatch_once(
        request_id=REQUEST, worker_id="normal-worker",
    )
    assert result.outcome is ScheduledWecomRouteOutcome.ACCEPTED
    assert repository.claim_calls == repository.normal_read_calls == 1
    assert repository.recover_calls == repository.read_calls == 0
    assert smart.normal_calls == 1 and smart.recovery_calls == []
    assert app.normal_calls == 0 and app.recovery_calls == []
