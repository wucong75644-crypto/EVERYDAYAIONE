"""Focused one-shot Scheduled WeCom Smart Robot orchestration coverage."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_receipt_hash,
)
from services.agent.runtime.application.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchService,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DeliveryClaim,
    DeliveryClaimKind,
    DeliveryClaimOutcome,
    DeliveryFence,
    DeliveryStatus,
    DispatchAttempt,
    DispatchChannel,
    DispatchOutcome,
    DispatchOutcomeReceipt,
    DispatchPayload,
    DispatchPayloadOutcome,
    DispatchPayloadVersions,
    ItemStatus,
    ProviderDispatchIdentity,
    ReceiptMetadata,
    ReceiptType,
    RecordOutcome,
    WecomAppDispatchTarget,
    WecomSmartRobotDispatchTarget,
)
from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchError,
    SmartRobotDispatchOutcome,
)
from services.wecom.ws_outbound import (
    WecomOutboundAckResult,
    WecomOutboundErrorClass,
    WecomOutboundStatus,
)


INTENT = "11111111-1111-1111-1111-111111111111"
ITEM = "22222222-2222-2222-2222-222222222222"
CLAIM_REQUEST = "33333333-3333-3333-3333-333333333333"
LEASE = "44444444-4444-4444-4444-444444444444"
ATTEMPT = "55555555-5555-5555-5555-555555555555"
ORG = "66666666-6666-6666-6666-666666666666"
RUN = "77777777-7777-7777-7777-777777777777"
NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def test_service_has_no_global_claim_payload_read_or_legacy_transport() -> None:
    source = Path(
        "backend/services/agent/runtime/application/scheduled_wecom_smart_dispatch.py",
    ).read_text()
    for forbidden in (
        ".claim_delivery(", ".read_dispatch_payload(", ".terminalize_unsupported(",
        ".send_proactive(", ".send_msg(",
    ):
        assert forbidden not in source


def _claim() -> DeliveryClaim:
    return DeliveryClaim(
        outcome=DeliveryClaimOutcome.CLAIMED,
        kind=DeliveryClaimKind.INITIAL,
        fence=DeliveryFence(
            intent_id=INTENT, item_id=ITEM, claim_request_id=CLAIM_REQUEST,
            lease_token=LEASE, worker_id="smart-worker",
            delivery_state_version=3, item_state_version=2,
        ),
        lease_seconds=60, lease_expires_at=NOW,
        previous_claim_request_id=None,
    )


def _payload(channel: DispatchChannel = DispatchChannel.SMART_ROBOT) -> DispatchPayload:
    target = (
        WecomSmartRobotDispatchTarget(org_id=ORG, chatid="群聊-secret-甲")
        if channel is DispatchChannel.SMART_ROBOT
        else WecomAppDispatchTarget(org_id=ORG, corp_id="corp", wecom_userid="member")
    )
    return DispatchPayload(
        outcome=DispatchPayloadOutcome.PAYLOAD, payload_revision=2,
        scheduled_run_id=RUN, intent_id=INTENT, item_id=ITEM,
        item_key="a" * 64, ordinal=1, item_kind="text", source_role="text",
        source_revision=1, source_identity_hash="b" * 64,
        content_identity_hash="c" * 64, result_hash="d" * 64,
        target_hash="e" * 64, channel=channel, target=target,
        provider_revision=4, delivery_state_version=3, item_state_version=2,
        message_type="text", text="任务完成：https://example.com/result",
        payload_hash="f" * 64,
    )


class _Repository:
    def __init__(
        self, *, prepare_readback: bool = False, start_readback: bool = False,
        response_loss_replay: bool = False, record_error: bool = False,
    ) -> None:
        self.prepare_readback = prepare_readback
        self.start_readback = start_readback
        self.response_loss_replay = response_loss_replay
        self.record_error = record_error
        self.attempt: DispatchAttempt | None = None
        self.prepare_calls: list[ProviderDispatchIdentity] = []
        self.start_calls = 0
        self.outcome_calls: list[tuple[str, DispatchOutcome, object]] = []

    async def prepare_dispatch(
        self, claim: DeliveryClaim, identity: ProviderDispatchIdentity,
    ) -> DispatchAttempt:
        self.prepare_calls.append(identity)
        if self.attempt is None:
            self.attempt = DispatchAttempt(
                outcome=(
                    AttemptOperationOutcome.READBACK
                    if self.prepare_readback else AttemptOperationOutcome.PREPARED
                ),
                fence=claim.fence, attempt_id=ATTEMPT, attempt_number=1,
                identity=identity,
                payload_versions=DispatchPayloadVersions(
                    delivery_state_version=claim.fence.delivery_state_version,
                    item_state_version=claim.fence.item_state_version,
                ),
                status=AttemptStatus.PREPARED,
            )
            return self.attempt
        return replace(self.attempt, outcome=AttemptOperationOutcome.READBACK)

    async def start_dispatch(self, attempt: DispatchAttempt) -> DispatchAttempt:
        self.start_calls += 1
        self.attempt = replace(
            attempt,
            outcome=(
                AttemptOperationOutcome.READBACK
                if self.start_readback else AttemptOperationOutcome.DISPATCH_STARTED
            ),
            status=AttemptStatus.DISPATCH_STARTED,
        )
        return self.attempt

    async def record_dispatch_outcome(
        self, attempt: DispatchAttempt, *, request_id: str,
        dispatch_outcome: DispatchOutcome, evidence: object,
    ) -> DispatchOutcomeReceipt:
        call = (request_id, dispatch_outcome, evidence)
        self.outcome_calls.append(call)
        if self.response_loss_replay:
            self.outcome_calls.append(call)
        if self.record_error:
            raise RuntimeError("database unavailable")
        status = AttemptStatus(dispatch_outcome.value)
        self.attempt = replace(attempt, outcome=AttemptOperationOutcome.READBACK, status=status)
        item_status = {
            DispatchOutcome.ACCEPTED: ItemStatus.ACCEPTED,
            DispatchOutcome.REJECTED: ItemStatus.FAILED,
            DispatchOutcome.UNKNOWN: ItemStatus.UNKNOWN,
        }[dispatch_outcome]
        delivery_status = {
            DispatchOutcome.ACCEPTED: DeliveryStatus.COMPLETED,
            DispatchOutcome.REJECTED: DeliveryStatus.FAILED,
            DispatchOutcome.UNKNOWN: DeliveryStatus.UNKNOWN,
        }[dispatch_outcome]
        return DispatchOutcomeReceipt(
            outcome=(RecordOutcome.READBACK if self.response_loss_replay else RecordOutcome.RECORDED),
            request_id=request_id, intent_id=INTENT, item_id=ITEM,
            attempt_id=ATTEMPT, dispatch_outcome=dispatch_outcome,
            evidence=evidence, attempt_status=status, item_status=item_status,
            delivery_status=delivery_status, delivery_state_version=4,
            item_state_version=3,
        )


class _Transport:
    def __init__(
        self, status: WecomOutboundStatus = WecomOutboundStatus.ACKNOWLEDGED,
        *, errcode: int | None = None, mismatch: bool = False,
        error: BaseException | None = None, delay: float = 0,
        org_id: str = ORG, is_connected: bool = True,
    ) -> None:
        self.status = status
        self.errcode = errcode
        self.mismatch = mismatch
        self.error = error
        self.delay = delay
        self.org_id = org_id
        self.is_connected = is_connected
        self.resolve_calls: list[str] = []
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []

    async def resolve_smart_transport(self, org_id: str) -> _Transport | None:
        self.resolve_calls.append(org_id)
        return self

    async def send_proactive_typed(
        self, provider_request_id: str, chatid: str,
        msgtype: str, content: dict[str, str],
    ) -> WecomOutboundAckResult:
        self.calls.append((provider_request_id, chatid, msgtype, content))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        error_class = (
            WecomOutboundErrorClass.PROVIDER_REJECTED
            if self.status is WecomOutboundStatus.REJECTED
            else None
        )
        return WecomOutboundAckResult(
            "different-provider" if self.mismatch else provider_request_id,
            self.status, self.errcode, error_class,
        )


class _GateTransport(_Transport):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_proactive_typed(
        self, provider_request_id: str, chatid: str,
        msgtype: str, content: dict[str, str],
    ) -> WecomOutboundAckResult:
        self.calls.append((provider_request_id, chatid, msgtype, content))
        self.started.set()
        await self.release.wait()
        return WecomOutboundAckResult(
            provider_request_id, WecomOutboundStatus.ACKNOWLEDGED, None, None,
        )


class _ReversedPrepareRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.fresh_started = asyncio.Event()
        self.release_fresh = asyncio.Event()

    async def prepare_dispatch(
        self, claim: DeliveryClaim, identity: ProviderDispatchIdentity,
    ) -> DispatchAttempt:
        self.prepare_calls.append(identity)
        if self.attempt is None:
            self.attempt = DispatchAttempt(
                outcome=AttemptOperationOutcome.PREPARED,
                fence=claim.fence, attempt_id=ATTEMPT, attempt_number=1,
                identity=identity,
                payload_versions=DispatchPayloadVersions(
                    delivery_state_version=claim.fence.delivery_state_version,
                    item_state_version=claim.fence.item_state_version,
                ),
                status=AttemptStatus.PREPARED,
            )
            self.fresh_started.set()
            await self.release_fresh.wait()
            return self.attempt
        return replace(self.attempt, outcome=AttemptOperationOutcome.READBACK)


@pytest.mark.asyncio
async def test_ack_records_accepted_with_exact_markdown_and_typed_evidence() -> None:
    repository = _Repository()
    transport = _Transport()
    service = ScheduledWecomSmartDispatchService(repository, transport)

    result = await service.dispatch_claimed(_claim(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.ACCEPTED
    identity = repository.prepare_calls[0]
    assert transport.calls == [(
        identity.provider_request_id, "群聊-secret-甲", "markdown",
        {"content": "任务完成：https://example.com/result"},
    )]
    evidence = result.dispatch_receipt.evidence
    assert evidence.receipt_type is ReceiptType.WECOM_SMART_ROBOT
    assert evidence.receipt_code == "acknowledged"
    assert evidence.metadata == ReceiptMetadata()
    assert evidence.receipt_hash == scheduled_wecom_receipt_hash(
        dispatch_outcome=DispatchOutcome.ACCEPTED,
        receipt_type=ReceiptType.WECOM_SMART_ROBOT,
        receipt_code="acknowledged", metadata=ReceiptMetadata(), identity=identity,
    )


@pytest.mark.asyncio
async def test_provider_rejection_records_only_allowlisted_evidence() -> None:
    repository = _Repository()
    transport = _Transport(WecomOutboundStatus.REJECTED, errcode=40013)

    result = await ScheduledWecomSmartDispatchService(
        repository, transport,
    ).dispatch_claimed(_claim(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.REJECTED
    evidence = result.dispatch_receipt.evidence
    assert evidence.receipt_code == "provider_rejected"
    assert evidence.metadata == ReceiptMetadata(wecom_errcode=40013)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [WecomOutboundStatus.UNKNOWN, WecomOutboundStatus.NOT_STARTED],
)
async def test_non_definitive_transport_status_records_unknown(status) -> None:
    repository = _Repository()
    result = await ScheduledWecomSmartDispatchService(
        repository, _Transport(status),
    ).dispatch_claimed(_claim(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.UNKNOWN
    assert repository.outcome_calls[0][1:] == (DispatchOutcome.UNKNOWN, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport", [
        _Transport(mismatch=True),
        _Transport(error=RuntimeError("private token")),
        _Transport(WecomOutboundStatus.REJECTED, errcode=2**40),
    ],
)
async def test_identity_mismatch_and_transport_exception_record_unknown(transport) -> None:
    repository = _Repository()

    result = await ScheduledWecomSmartDispatchService(
        repository, transport,
    ).dispatch_claimed(_claim(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.UNKNOWN
    assert repository.outcome_calls[0][1:] == (DispatchOutcome.UNKNOWN, None)


@pytest.mark.asyncio
async def test_transport_cancellation_after_start_records_unknown_then_reraises() -> None:
    repository = _Repository()
    service = ScheduledWecomSmartDispatchService(
        repository, _Transport(error=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await service.dispatch_claimed(_claim(), _payload())

    assert repository.outcome_calls[0][1:] == (DispatchOutcome.UNKNOWN, None)
    assert repository.attempt.status is AttemptStatus.UNKNOWN


@pytest.mark.asyncio
async def test_cancellation_preserved_when_unknown_persistence_fails() -> None:
    repository = _Repository(record_error=True)
    service = ScheduledWecomSmartDispatchService(
        repository, _Transport(error=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await service.dispatch_claimed(_claim(), _payload())

    assert repository.attempt.status is AttemptStatus.DISPATCH_STARTED
    assert len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_owner_caller_cancellation_does_not_release_shared_flight() -> None:
    repository = _Repository()
    transport = _GateTransport()
    service = ScheduledWecomSmartDispatchService(repository, transport)
    owner = asyncio.create_task(service.dispatch_claimed(_claim(), _payload()))
    await transport.started.wait()
    duplicate = asyncio.create_task(service.dispatch_claimed(_claim(), _payload()))

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    transport.release.set()
    result = await duplicate

    assert result.outcome is SmartRobotDispatchOutcome.ACCEPTED
    assert len(repository.prepare_calls) == 1
    assert repository.start_calls == 1
    assert len(transport.calls) == 1
    assert len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_non_smart_and_route_drift_have_zero_side_effects() -> None:
    for claim, payload in (
        (_claim(), _payload(DispatchChannel.APP)),
        (
            _claim(),
            replace(_payload(), outcome=DispatchPayloadOutcome.UNSUPPORTED),
        ),
        (_claim(), replace(_payload(), item_id="88888888-8888-8888-8888-888888888888")),
        (replace(_claim(), outcome=DeliveryClaimOutcome.FENCED), _payload()),
    ):
        repository = _Repository()
        transport = _Transport()
        with pytest.raises(ScheduledWecomSmartDispatchError):
            await ScheduledWecomSmartDispatchService(
                repository, transport,
            ).dispatch_claimed(claim, payload)
        assert repository.prepare_calls == []
        assert transport.resolve_calls == []
        assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository", [_Repository(prepare_readback=True), _Repository(start_readback=True)],
)
async def test_prepare_or_start_readback_never_sends(repository) -> None:
    transport = _Transport()
    result = await ScheduledWecomSmartDispatchService(
        repository, transport,
    ).dispatch_claimed(_claim(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.ALREADY_PERSISTED
    assert transport.calls == []


@pytest.mark.asyncio
async def test_response_loss_replay_uses_identical_outcome_request() -> None:
    repository = _Repository(response_loss_replay=True)
    service = ScheduledWecomSmartDispatchService(repository, _Transport())

    first = await service.dispatch_claimed(_claim(), _payload())
    second = await service.dispatch_claimed(_claim(), _payload())

    assert first.dispatch_receipt.outcome is RecordOutcome.READBACK
    assert second.outcome is SmartRobotDispatchOutcome.ALREADY_PERSISTED
    assert repository.outcome_calls[0] == repository.outcome_calls[1]
    assert repository.prepare_calls[0] == repository.prepare_calls[1]


@pytest.mark.asyncio
async def test_50_same_service_calls_use_one_transport() -> None:
    repository = _Repository()
    transport = _Transport(delay=0.02)
    service = ScheduledWecomSmartDispatchService(repository, transport)

    results = await asyncio.gather(*(
        service.dispatch_claimed(_claim(), _payload()) for _ in range(50)
    ))

    assert all(result.outcome is SmartRobotDispatchOutcome.ACCEPTED for result in results)
    assert len(repository.prepare_calls) == 1
    assert transport.resolve_calls == [ORG]
    assert len(transport.calls) == 1
    assert repository.start_calls == 1
    assert len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_reversed_prepare_response_order_keeps_one_shared_owner() -> None:
    repository = _ReversedPrepareRepository()
    transport = _Transport()
    service = ScheduledWecomSmartDispatchService(repository, transport)
    callers = [
        asyncio.create_task(service.dispatch_claimed(_claim(), _payload()))
        for _ in range(50)
    ]
    await repository.fresh_started.wait()

    readback = await repository.prepare_dispatch(
        _claim(), repository.prepare_calls[0],
    )
    assert readback.outcome is AttemptOperationOutcome.READBACK
    repository.release_fresh.set()
    results = await asyncio.gather(*callers)

    assert all(result.outcome is SmartRobotDispatchOutcome.ACCEPTED for result in results)
    assert all(result == results[0] for result in results)
    assert len(repository.prepare_calls) == 2
    assert repository.start_calls == 1
    assert len(transport.calls) == 1
    assert len(repository.outcome_calls) == 1
