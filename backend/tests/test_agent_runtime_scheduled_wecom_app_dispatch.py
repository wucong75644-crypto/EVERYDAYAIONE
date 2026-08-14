"""Focused one-shot Scheduled WeCom App orchestration coverage."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.agent.runtime.application.scheduled_wecom_app_dispatch import (
    ScheduledWecomAppDispatchService,
)
from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_app_identity,
    scheduled_wecom_receipt_hash,
    scheduled_wecom_smart_identity,
)
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import (
    AppDispatchOutcome,
    ScheduledWecomAppBinding,
    ScheduledWecomAppDispatchError,
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
from services.wecom.app_outbound import (
    WecomAppOutboundErrorClass,
    WecomAppOutboundReceipt,
    WecomAppOutboundStatus,
)


INTENT = "11111111-1111-1111-1111-111111111111"
ITEM = "22222222-2222-2222-2222-222222222222"
CLAIM_REQUEST = "33333333-3333-3333-3333-333333333333"
LEASE = "44444444-4444-4444-4444-444444444444"
ATTEMPT = "55555555-5555-5555-5555-555555555555"
ORG = "66666666-6666-6666-6666-666666666666"
RUN = "77777777-7777-7777-7777-777777777777"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _claim() -> DeliveryClaim:
    return DeliveryClaim(
        outcome=DeliveryClaimOutcome.CLAIMED,
        kind=DeliveryClaimKind.INITIAL,
        fence=DeliveryFence(
            intent_id=INTENT,
            item_id=ITEM,
            claim_request_id=CLAIM_REQUEST,
            lease_token=LEASE,
            worker_id="app-worker",
            delivery_state_version=3,
            item_state_version=2,
        ),
        lease_seconds=60,
        lease_expires_at=NOW,
        previous_claim_request_id=None,
    )


def _payload(channel: DispatchChannel = DispatchChannel.APP) -> DispatchPayload:
    target = (
        WecomAppDispatchTarget(org_id=ORG, corp_id="企业-secret-甲", wecom_userid="成员-甲")
        if channel is DispatchChannel.APP
        else WecomSmartRobotDispatchTarget(org_id=ORG, chatid="chat-1")
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
        text="任务完成：https://example.com/result",
        payload_hash="f" * 64,
    )


class _Repository:
    def __init__(
        self,
        *,
        prepare_readback: bool = False,
        start_readback: bool = False,
        response_loss_replay: bool = False,
        record_error: bool = False,
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
                fence=claim.fence,
                attempt_id=ATTEMPT,
                attempt_number=1,
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
        self,
        attempt: DispatchAttempt,
        *,
        request_id: str,
        dispatch_outcome: DispatchOutcome,
        evidence: object,
    ) -> DispatchOutcomeReceipt:
        call = (request_id, dispatch_outcome, evidence)
        self.outcome_calls.append(call)
        if self.response_loss_replay:
            self.outcome_calls.append(call)
        if self.record_error:
            raise RuntimeError("database unavailable")
        status = AttemptStatus(dispatch_outcome.value)
        self.attempt = replace(attempt, outcome=AttemptOperationOutcome.READBACK, status=status)
        return DispatchOutcomeReceipt(
            outcome=(RecordOutcome.READBACK if self.response_loss_replay else RecordOutcome.RECORDED),
            request_id=request_id,
            intent_id=INTENT,
            item_id=ITEM,
            attempt_id=ATTEMPT,
            dispatch_outcome=dispatch_outcome,
            evidence=evidence,
            attempt_status=status,
            item_status={
                DispatchOutcome.ACCEPTED: ItemStatus.ACCEPTED,
                DispatchOutcome.REJECTED: ItemStatus.FAILED,
                DispatchOutcome.UNKNOWN: ItemStatus.UNKNOWN,
            }[dispatch_outcome],
            delivery_status={
                DispatchOutcome.ACCEPTED: DeliveryStatus.COMPLETED,
                DispatchOutcome.REJECTED: DeliveryStatus.FAILED,
                DispatchOutcome.UNKNOWN: DeliveryStatus.UNKNOWN,
            }[dispatch_outcome],
            delivery_state_version=4,
            item_state_version=3,
        )


class _Transport:
    def __init__(
        self,
        status: WecomAppOutboundStatus = WecomAppOutboundStatus.ACKNOWLEDGED,
        *,
        errcode: int | None = 0,
        provider_message_id: str | None = "msg-001",
        error_class: WecomAppOutboundErrorClass | None = None,
        mismatch: bool = False,
        error: BaseException | None = None,
        delay: float = 0,
    ) -> None:
        self.status = status
        self.errcode = errcode
        self.provider_message_id = provider_message_id
        self.error_class = error_class
        self.mismatch = mismatch
        self.error = error
        self.delay = delay
        self.calls: list[tuple[str, str, object]] = []

    async def send_typed(
        self, *, provider_request_id: str, target: str, payload: object,
    ) -> WecomAppOutboundReceipt:
        self.calls.append((provider_request_id, target, payload))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return WecomAppOutboundReceipt(
            provider_request_id=("different-provider" if self.mismatch else provider_request_id),
            status=self.status,
            errcode=self.errcode,
            provider_message_id=self.provider_message_id,
            error_class=self.error_class,
        )


def _binding(transport: _Transport, **changes: object) -> ScheduledWecomAppBinding:
    values = {"org_id": ORG, "corp_id": "企业-secret-甲", "agent_id": 1000006}
    values.update(changes)
    return ScheduledWecomAppBinding(transport=transport, **values)


def test_service_has_no_global_claim_config_or_legacy_sender() -> None:
    source = Path(
        "backend/services/agent/runtime/application/scheduled_wecom_app_dispatch.py",
    ).read_text()
    for forbidden in (
        ".claim_delivery(", ".read_dispatch_payload(", ".terminalize_unsupported(",
        "app_message_sender", "get_access_token", "agent_secret", "access_token",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_ack_sends_exact_text_payload_and_records_allowlisted_evidence() -> None:
    repository = _Repository()
    transport = _Transport()
    result = await ScheduledWecomAppDispatchService(repository).dispatch_claimed(
        _claim(), _payload(), _binding(transport),
    )

    assert result.outcome is AppDispatchOutcome.ACCEPTED
    identity = repository.prepare_calls[0]
    assert transport.calls == [(
        identity.provider_request_id,
        "成员-甲",
        {
            "touser": "成员-甲",
            "msgtype": "text",
            "agentid": 1000006,
            "text": {"content": "任务完成：https://example.com/result"},
        },
    )]
    evidence = result.dispatch_receipt.evidence
    assert evidence.receipt_type is ReceiptType.WECOM_APP
    assert evidence.receipt_code == "acknowledged"
    assert evidence.metadata == ReceiptMetadata(
        provider_message_id="msg-001", wecom_errcode=0,
    )
    assert evidence.receipt_hash == scheduled_wecom_receipt_hash(
        dispatch_outcome=DispatchOutcome.ACCEPTED,
        receipt_type=ReceiptType.WECOM_APP,
        receipt_code="acknowledged",
        metadata=evidence.metadata,
        identity=identity,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_class", "errcode", "receipt_code"),
    (
        (WecomAppOutboundErrorClass.PROVIDER_REJECTED, 40013, "provider_rejected"),
        (WecomAppOutboundErrorClass.PROVIDER_PARTIAL_REJECTED, 0, "provider_partial_rejected"),
    ),
)
async def test_provider_and_partial_rejection_record_typed_evidence(
    error_class: WecomAppOutboundErrorClass, errcode: int, receipt_code: str,
) -> None:
    repository = _Repository()
    transport = _Transport(
        WecomAppOutboundStatus.REJECTED,
        errcode=errcode,
        provider_message_id=None,
        error_class=error_class,
    )
    result = await ScheduledWecomAppDispatchService(repository).dispatch_claimed(
        _claim(), _payload(), _binding(transport),
    )

    assert result.outcome is AppDispatchOutcome.REJECTED
    assert result.dispatch_receipt.evidence.receipt_code == receipt_code
    assert result.dispatch_receipt.evidence.metadata == ReceiptMetadata(
        wecom_errcode=errcode,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport",
    (
        _Transport(WecomAppOutboundStatus.UNKNOWN, errcode=None, provider_message_id=None),
        _Transport(WecomAppOutboundStatus.NOT_STARTED, errcode=None, provider_message_id=None),
        _Transport(mismatch=True),
        _Transport(error=RuntimeError("secret token path")),
        _Transport(provider_message_id="unsafe message id"),
    ),
)
async def test_non_definitive_malformed_or_exception_records_unknown_without_leak(
    transport: _Transport,
) -> None:
    repository = _Repository()
    result = await ScheduledWecomAppDispatchService(repository).dispatch_claimed(
        _claim(), _payload(), _binding(transport),
    )

    assert result.outcome is AppDispatchOutcome.UNKNOWN
    assert repository.outcome_calls[0][1:] == (DispatchOutcome.UNKNOWN, None)
    exposed = repr(result) + repr(repository.outcome_calls)
    assert "secret token path" not in exposed
    assert "access_token" not in repr(transport.calls)


@pytest.mark.asyncio
async def test_binding_route_and_non_app_fail_before_prepare() -> None:
    for claim, payload, changes in (
        (_claim(), _payload(DispatchChannel.SMART_ROBOT), {}),
        (_claim(), _payload(), {"org_id": "88888888-8888-8888-8888-888888888888"}),
        (_claim(), _payload(), {"corp_id": "other-corp"}),
        (_claim(), _payload(), {"agent_id": 0}),
        (replace(_claim(), outcome=DeliveryClaimOutcome.FENCED), _payload(), {}),
    ):
        repository = _Repository()
        transport = _Transport()
        with pytest.raises(ScheduledWecomAppDispatchError):
            await ScheduledWecomAppDispatchService(repository).dispatch_claimed(
                claim, payload, _binding(transport, **changes),
            )
        assert repository.prepare_calls == []
        assert transport.calls == []


def test_app_identity_is_deterministic_domain_separated_and_binding_frozen() -> None:
    payload = _payload()
    first = scheduled_wecom_app_identity(
        payload, org_id=ORG, corp_id="企业-secret-甲", agent_id=1000006,
    )
    same = scheduled_wecom_app_identity(
        payload, org_id=ORG, corp_id="企业-secret-甲", agent_id=1000006,
    )
    changed = scheduled_wecom_app_identity(
        payload, org_id=ORG, corp_id="企业-secret-甲", agent_id=1000007,
    )

    assert first == same
    assert first != changed
    assert first != scheduled_wecom_smart_identity(payload)
    assert first.provider_request_id.startswith("scheduled-wecom-app:")
    assert len(first.idempotency_key) == 64


def test_binding_repr_excludes_constructed_transport() -> None:
    binding = _binding(_Transport(error=RuntimeError("secret token")))
    assert "transport" not in repr(binding)
    assert "secret token" not in repr(binding)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository", (_Repository(prepare_readback=True), _Repository(start_readback=True)),
)
async def test_prepare_or_start_readback_never_sends(repository: _Repository) -> None:
    transport = _Transport()
    result = await ScheduledWecomAppDispatchService(repository).dispatch_claimed(
        _claim(), _payload(), _binding(transport),
    )
    assert result.outcome is AppDispatchOutcome.ALREADY_PERSISTED
    assert transport.calls == []


@pytest.mark.asyncio
async def test_transport_cancellation_records_unknown_then_reraises() -> None:
    repository = _Repository()
    transport = _Transport(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await ScheduledWecomAppDispatchService(repository).dispatch_claimed(
            _claim(), _payload(), _binding(transport),
        )
    assert repository.outcome_calls[0][1:] == (DispatchOutcome.UNKNOWN, None)
    assert repository.attempt.status is AttemptStatus.UNKNOWN


@pytest.mark.asyncio
async def test_cancellation_persistence_failure_leaves_started_for_recovery() -> None:
    repository = _Repository(record_error=True)
    transport = _Transport(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await ScheduledWecomAppDispatchService(repository).dispatch_claimed(
            _claim(), _payload(), _binding(transport),
        )
    assert len(repository.outcome_calls) == 1
    assert repository.attempt.status is AttemptStatus.DISPATCH_STARTED


@pytest.mark.asyncio
async def test_response_loss_replay_uses_identical_outcome_request() -> None:
    repository = _Repository(response_loss_replay=True)
    transport = _Transport()
    service = ScheduledWecomAppDispatchService(repository)
    first = await service.dispatch_claimed(_claim(), _payload(), _binding(transport))
    second = await service.dispatch_claimed(_claim(), _payload(), _binding(transport))

    assert first.dispatch_receipt.outcome is RecordOutcome.READBACK
    assert second.outcome is AppDispatchOutcome.ALREADY_PERSISTED
    assert repository.outcome_calls[0] == repository.outcome_calls[1]
    assert repository.prepare_calls[0] == repository.prepare_calls[1]
