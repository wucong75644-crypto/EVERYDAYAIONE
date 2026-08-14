"""Scheduled WeCom reconciliation orchestration contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_reconcile_readback_hash,
    scheduled_wecom_request_id,
)
from services.agent.runtime.application.scheduled_wecom_reconcile import (
    ScheduledWecomReconcileError,
    ScheduledWecomReconcileService,
)
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptStatus,
    DeliveryStatus,
    DispatchPhase,
    ItemStatus,
    ProviderDispatchIdentity,
    ReceiptMetadata,
    ReceiptType,
    ReconcileClaim,
    ReconcileClaimOutcome,
    ReconcileResult,
)
from services.wecom.ws_outbound import (
    WecomOutboundAckResult,
    WecomOutboundErrorClass,
    WecomOutboundStatus,
)


ORG = "11111111-1111-1111-1111-111111111111"
CLAIM_REQUEST = "22222222-2222-2222-2222-222222222222"
CLAIM_CALL = "33333333-3333-3333-3333-333333333333"
PROVIDER_DIGEST = "a" * 64
IDEMPOTENCY = "b" * 64
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
MIGRATION = Path(__file__).parents[1] / (
    "migrations/227_43_agent_runtime_scheduled_wecom_reconcile_still_unknown.sql"
)


class _Repository:
    def __init__(
        self, claim: ReconcileClaim | None, claim_error: Exception | None = None,
    ) -> None:
        self.claim = claim
        self.claim_error = claim_error
        self.claim_calls: list[dict[str, object]] = []
        self.records: list[tuple[str, ReconcileResult, object, int | None]] = []

    async def claim_reconcile(self, **kwargs: object) -> ReconcileClaim | None:
        self.claim_calls.append(kwargs)
        if self.claim_error is not None:
            raise self.claim_error
        return self.claim

    async def record_still_unknown(
        self, _claim: ReconcileClaim, *, request_id: str,
        evidence: object, delay_seconds: int,
    ) -> object:
        self.records.append((
            request_id, ReconcileResult.STILL_UNKNOWN, evidence, delay_seconds,
        ))
        return object()

    async def record_definitive(
        self, _claim: ReconcileClaim, *, request_id: str,
        result: ReconcileResult, evidence: object,
    ) -> object:
        self.records.append((request_id, result, evidence, None))
        return object()


class _Transport:
    def __init__(self, result: WecomOutboundAckResult | None) -> None:
        self.org_id = ORG
        self.result = result
        self.lookup_calls: list[str] = []
        self.send_calls = 0

    def lookup_outbound_result(
        self, provider_request_id: str,
    ) -> WecomOutboundAckResult | None:
        self.lookup_calls.append(provider_request_id)
        return self.result

    async def send_proactive_typed(self, *_args: object) -> object:
        self.send_calls += 1
        raise AssertionError("reconcile must never send")


class _Resolver:
    def __init__(self, transport: _Transport | None) -> None:
        self.transport = transport
        self.calls: list[str] = []

    async def resolve_smart_readback(self, org_id: str) -> _Transport | None:
        self.calls.append(org_id)
        return self.transport


def _claim(prefix: str = "scheduled-wecom-smart:") -> ReconcileClaim:
    return ReconcileClaim(
        outcome=ReconcileClaimOutcome.CLAIMED,
        request_id=CLAIM_REQUEST,
        intent_id="44444444-4444-4444-4444-444444444444",
        org_id=ORG,
        item_id="55555555-5555-5555-5555-555555555555",
        attempt_id="66666666-6666-6666-6666-666666666666",
        worker_id="reconciler",
        reconcile_token="77777777-7777-7777-7777-777777777777",
        lease_seconds=60,
        lease_expires_at=NOW,
        claimed_lease_expires_at=NOW,
        claim_delivery_state_version=11,
        claim_item_state_version=7,
        delivery_state_version=11,
        item_state_version=7,
        delivery_status=DeliveryStatus.UNKNOWN,
        item_status=ItemStatus.UNKNOWN,
        attempt_status=AttemptStatus.UNKNOWN,
        dispatch_phase=DispatchPhase.AMBIGUOUS,
        identity=ProviderDispatchIdentity(
            provider_request_id=prefix + PROVIDER_DIGEST,
            idempotency_key=IDEMPOTENCY,
            provider_revision=4,
        ),
    )


@pytest.mark.asyncio
async def test_empty_claim_returns_without_readback_or_record() -> None:
    repository = _Repository(None)
    resolver = _Resolver(None)

    result = await ScheduledWecomReconcileService(
        repository, resolver,
    ).reconcile_once(request_id=CLAIM_CALL, worker_id="reconciler")

    assert result is None
    assert resolver.calls == []
    assert repository.records == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ack", "expected", "code", "errcode"),
    (
        (
            WecomOutboundAckResult(
                _claim().identity.provider_request_id,
                WecomOutboundStatus.ACKNOWLEDGED,
            ),
            ReconcileResult.ACCEPTED,
            "acknowledged",
            None,
        ),
        (
            WecomOutboundAckResult(
                _claim().identity.provider_request_id,
                WecomOutboundStatus.REJECTED,
                40013,
                WecomOutboundErrorClass.PROVIDER_REJECTED,
            ),
            ReconcileResult.REJECTED,
            "provider_rejected",
            40013,
        ),
    ),
)
async def test_smart_definitive_readback_records_exact_typed_evidence(
    ack: WecomOutboundAckResult,
    expected: ReconcileResult,
    code: str,
    errcode: int | None,
) -> None:
    claim = _claim()
    repository = _Repository(claim)
    transport = _Transport(ack)
    resolver = _Resolver(transport)

    await ScheduledWecomReconcileService(repository, resolver).reconcile_once(
        request_id=CLAIM_CALL, worker_id="reconciler", lease_seconds=75,
    )

    request_id, result, evidence, delay = repository.records[0]
    assert result is expected
    assert request_id == scheduled_wecom_request_id(
        "reconcile-result", CLAIM_REQUEST,
    )
    assert str(UUID(request_id)) == request_id
    assert evidence.receipt_type is ReceiptType.WECOM_SMART_ROBOT
    assert evidence.receipt_code == code
    assert evidence.metadata == ReceiptMetadata(wecom_errcode=errcode)
    assert evidence.receipt_hash == scheduled_wecom_reconcile_readback_hash(
        reconcile_result=expected,
        receipt_type=evidence.receipt_type,
        receipt_code=code,
        metadata=evidence.metadata,
        identity=claim.identity,
    )
    assert delay is None
    assert resolver.calls == [ORG]
    assert transport.lookup_calls == [claim.identity.provider_request_id]
    assert transport.send_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("errcode", "error_class"),
    (
        (0, WecomOutboundErrorClass.PROVIDER_REJECTED),
        (True, WecomOutboundErrorClass.PROVIDER_REJECTED),
        (None, WecomOutboundErrorClass.PROVIDER_REJECTED),
        (2**31, WecomOutboundErrorClass.PROVIDER_REJECTED),
        (-(2**31) - 1, WecomOutboundErrorClass.PROVIDER_REJECTED),
        (40013, WecomOutboundErrorClass.ACK_TIMEOUT),
    ),
)
async def test_contradictory_rejected_readback_fails_closed_without_record(
    errcode: object,
    error_class: WecomOutboundErrorClass,
) -> None:
    claim = _claim()
    repository = _Repository(claim)
    transport = _Transport(WecomOutboundAckResult(
        claim.identity.provider_request_id,
        WecomOutboundStatus.REJECTED,
        errcode,  # type: ignore[arg-type]
        error_class,
    ))

    with pytest.raises(
        ScheduledWecomReconcileError,
        match="REJECTED_EVIDENCE_INVALID",
    ):
        await ScheduledWecomReconcileService(
            repository, _Resolver(transport),
        ).reconcile_once(request_id=CLAIM_CALL, worker_id="reconciler")

    assert repository.records == []
    assert transport.lookup_calls == [claim.identity.provider_request_id]
    assert transport.send_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("readback", "code"),
    (
        (None, "lookup_miss_or_pending"),
        (
            WecomOutboundAckResult(
                _claim().identity.provider_request_id,
                WecomOutboundStatus.UNKNOWN,
            ),
            "unknown",
        ),
        (
            WecomOutboundAckResult(
                _claim().identity.provider_request_id,
                WecomOutboundStatus.NOT_STARTED,
            ),
            "not_started",
        ),
    ),
)
async def test_smart_nondefinitive_readback_records_still_unknown(
    readback: WecomOutboundAckResult | None,
    code: str,
) -> None:
    repository = _Repository(_claim())
    transport = _Transport(readback)

    await ScheduledWecomReconcileService(
        repository, _Resolver(transport),
    ).reconcile_once(request_id=CLAIM_CALL, worker_id="reconciler")

    _, result, evidence, delay = repository.records[0]
    assert result is ReconcileResult.STILL_UNKNOWN
    assert evidence.receipt_code == code
    assert evidence.metadata == ReceiptMetadata()
    assert delay == 60
    assert transport.send_calls == 0


@pytest.mark.asyncio
async def test_smart_unavailable_records_still_unknown_without_lookup() -> None:
    repository = _Repository(_claim())
    resolver = _Resolver(None)

    await ScheduledWecomReconcileService(repository, resolver).reconcile_once(
        request_id=CLAIM_CALL, worker_id="reconciler",
    )

    assert repository.records[0][1] is ReconcileResult.STILL_UNKNOWN
    assert repository.records[0][2].receipt_code == "readback_unavailable"


@pytest.mark.asyncio
async def test_app_records_unsupported_without_smart_or_credential_path() -> None:
    repository = _Repository(_claim("scheduled-wecom-app:"))
    resolver = _Resolver(_Transport(None))

    await ScheduledWecomReconcileService(repository, resolver).reconcile_once(
        request_id=CLAIM_CALL, worker_id="reconciler",
    )

    _, result, evidence, delay = repository.records[0]
    assert result is ReconcileResult.STILL_UNKNOWN
    assert evidence.receipt_type is ReceiptType.WECOM_APP
    assert evidence.receipt_code == "readback_unsupported"
    assert evidence.metadata == ReceiptMetadata()
    assert delay == 60
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_unknown_provider_prefix_fails_closed_without_readback_or_record() -> None:
    repository = _Repository(_claim("other-provider:"))
    resolver = _Resolver(None)

    with pytest.raises(ScheduledWecomReconcileError, match="IDENTITY_UNSUPPORTED"):
        await ScheduledWecomReconcileService(repository, resolver).reconcile_once(
            request_id=CLAIM_CALL, worker_id="reconciler",
        )

    assert resolver.calls == []
    assert repository.records == []


@pytest.mark.asyncio
async def test_claim_fence_error_produces_no_provider_side_effect() -> None:
    error = PersistenceContractError("reconcile_claim_fenced")
    repository = _Repository(None, error)
    transport = _Transport(None)
    resolver = _Resolver(transport)

    with pytest.raises(PersistenceContractError, match="fenced"):
        await ScheduledWecomReconcileService(repository, resolver).reconcile_once(
            request_id=CLAIM_CALL, worker_id="reconciler",
        )

    assert resolver.calls == []
    assert transport.lookup_calls == []
    assert transport.send_calls == 0


def test_reconcile_hash_matches_sql_contract_and_static_vector() -> None:
    claim = _claim()
    digest = scheduled_wecom_reconcile_readback_hash(
        reconcile_result=ReconcileResult.REJECTED,
        receipt_type=ReceiptType.WECOM_SMART_ROBOT,
        receipt_code="provider_rejected",
        metadata=ReceiptMetadata(wecom_errcode=40013),
        identity=claim.identity,
    )
    assert digest == "7a526ffadf2f2ad9529b126fa0e92bc66b2c43d0234bd8d9f75f28a4dd439422"

    sql = " ".join(MIGRATION.read_text().split())
    assert "everydayai.scheduled_wecom.reconcile_readback.v1" in sql
    for field in (
        "reconcile_result", "readback_type", "readback_code",
        "readback_metadata", "provider_request_id", "idempotency_key",
        "provider_revision",
    ):
        assert f"'{field}'" in sql
    assert "payload" not in sql.split(
        "CREATE TABLE agent_runtime_scheduled_wecom_reconcile_result_requests",
        maxsplit=1,
    )[0]
