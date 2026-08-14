"""Fake-database contracts for the typed Scheduled WeCom repository."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from psycopg import OperationalError

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.scheduled_wecom_delivery import (
    PostgresScheduledWecomDeliveryRepository,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptStatus,
    DispatchOutcome,
    DispatchPayloadVersions,
    ProviderDispatchIdentity,
    ProviderReceiptEvidence,
    ReceiptMetadata,
    ReceiptType,
    ReconcileResult,
)


REQUEST = "11111111-1111-1111-1111-111111111111"
INTENT = "22222222-2222-2222-2222-222222222222"
ITEM = "33333333-3333-3333-3333-333333333333"
LEASE = "44444444-4444-4444-4444-444444444444"
ATTEMPT = "55555555-5555-5555-5555-555555555555"
RESULT_REQUEST = "66666666-6666-6666-6666-666666666666"
RECONCILE_REQUEST = "77777777-7777-7777-7777-777777777777"
RECONCILE_TOKEN = "88888888-8888-8888-8888-888888888888"
ORG = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROVIDER_REQUEST = "provider-request-123"
IDEMPOTENCY = "a" * 64
HASH = "b" * 64
NOW = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)


class _Response:
    def __init__(self, data):
        self.data = data


class _Call:
    def __init__(self, database, name, params):
        self.database, self.name, self.params = database, name, params

    async def execute(self):
        self.database.calls.append((self.name, self.params))
        values = self.database.responses[self.name]
        value = values.pop(0) if isinstance(values, list) else values
        if isinstance(value, BaseException):
            raise value
        return _Response(deepcopy(value))


class _Database:
    def __init__(self, responses, kind=DatabaseAccessKind.WORKER):
        self.scope = DatabaseScope(None, None, kind, "scheduled-wecom-test")
        self.responses = responses
        self.calls = []

    def rpc(self, name, params):
        return _Call(self, name, params)

    def table(self, _name):
        raise AssertionError("table access is forbidden")


def _claim(outcome="claimed"):
    return {
        "outcome": outcome, "request_id": REQUEST, "claim_request_id": REQUEST,
        "intent_id": INTENT, "item_id": ITEM, "worker_id": "wecom-worker",
        "claim_kind": "continuation", "lease_token": LEASE, "lease_seconds": 60,
        "lease_expires_at": NOW, "previous_claim_request_id": None,
        "state_version": 7, "delivery_state_version": 7, "item_state_version": 3,
    }


def _attempt(outcome, status, delivery_version, item_version):
    return {
        "outcome": outcome, "attempt_id": ATTEMPT, "item_id": ITEM,
        "attempt_number": 1, "provider_request_id": PROVIDER_REQUEST,
        "idempotency_key": IDEMPOTENCY, "provider_revision": 4, "status": status,
        "delivery_state_version": delivery_version,
        "item_state_version": item_version,
    }


def _outcome(outcome="recorded", dispatch="accepted"):
    unknown = dispatch == "unknown"
    return {
        "outcome": outcome, "request_id": RESULT_REQUEST, "intent_id": INTENT,
        "item_id": ITEM, "attempt_id": ATTEMPT, "dispatch_outcome": dispatch,
        "receipt_type": None if unknown else "wecom_app",
        "receipt_hash": None if unknown else HASH,
        "receipt_code": None if unknown else "ok",
        "receipt_metadata": {} if unknown else {"http_status": 200, "wecom_errcode": 0},
        "attempt_status": dispatch,
        "item_status": "unknown" if unknown else "accepted",
        "delivery_status": "unknown" if unknown else "completed",
        "delivery_state_version": 10, "item_state_version": 6,
    }


def _reconcile(outcome="claimed"):
    return {
        "outcome": outcome, "request_id": RECONCILE_REQUEST, "intent_id": INTENT,
        "org_id": ORG, "item_id": ITEM, "attempt_id": ATTEMPT, "worker_id": "reconciler",
        "reconcile_token": RECONCILE_TOKEN, "lease_seconds": 60,
        "lease_expires_at": NOW, "claimed_lease_expires_at": NOW,
        "claim_delivery_state_version": 11, "claim_item_state_version": 7,
        "delivery_state_version": 11 if outcome != "renewed" else 12,
        "item_state_version": 7, "delivery_status": "unknown",
        "item_status": "unknown", "attempt_status": "unknown",
        "dispatch_phase": "ambiguous", "provider_request_id": PROVIDER_REQUEST,
        "idempotency_key": IDEMPOTENCY, "provider_revision": 4,
    }


def _reconcile_result(result="still_unknown", outcome="recorded"):
    definitive = result != "still_unknown"
    row = {
        "outcome": outcome, "request_id": RESULT_REQUEST,
        "claim_request_id": RECONCILE_REQUEST, "intent_id": INTENT,
        "item_id": ITEM, "attempt_id": ATTEMPT, "reconcile_result": result,
        "readback_type": "wecom_app", "readback_hash": HASH,
        "readback_code": "ok", "readback_metadata": {"trace_id": "trace-1"},
        "attempt_status": result if definitive else "unknown",
        "dispatch_phase": "receipt_recorded" if definitive else "ambiguous",
        "item_status": "accepted" if result == "accepted" else (
            "failed" if result == "rejected" else "reconcile_required"
        ),
        "delivery_status": "completed" if result == "accepted" else (
            "failed" if result == "rejected" else "reconcile_required"
        ),
        "delivery_state_version": 12, "item_state_version": 8,
    }
    if definitive:
        row["resolved_at"] = NOW
    else:
        row.update({"delay_seconds": 300, "next_attempt_at": NOW})
    return row


def _identity():
    return ProviderDispatchIdentity(
        provider_request_id=PROVIDER_REQUEST,
        idempotency_key=IDEMPOTENCY,
        provider_revision=4,
    )


def _evidence():
    return ProviderReceiptEvidence(
        receipt_type=ReceiptType.WECOM_APP,
        receipt_hash=HASH,
        receipt_code="ok",
        metadata=ReceiptMetadata(http_status=200, wecom_errcode=0),
    )


@pytest.mark.asyncio
async def test_dispatch_chain_uses_v2_versions_and_explicit_v1_outcome_params() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": _claim(),
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2": _attempt("prepared", "prepared", 8, 4),
        "start_agent_runtime_scheduled_wecom_dispatch_v2": _attempt(
            "dispatch_started", "dispatch_started", 9, 5,
        ),
        "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2": _attempt(
            "readback", "dispatch_started", 9, 5,
        ),
        "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1": _outcome(),
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_delivery(
        request_id=REQUEST, worker_id="wecom-worker",
    )
    assert claim is not None
    prepared = await repository.prepare_dispatch(claim, _identity())
    assert prepared.fence.delivery_state_version == 8
    started = await repository.start_dispatch(prepared)
    assert started.fence.item_state_version == 5
    readback = await repository.read_attempt(started)
    assert readback.status is AttemptStatus.DISPATCH_STARTED
    receipt = await repository.record_dispatch_outcome(
        readback, request_id=RESULT_REQUEST,
        dispatch_outcome=DispatchOutcome.ACCEPTED, evidence=_evidence(),
    )
    assert receipt.delivery_state_version == 10
    names = [name for name, _ in database.calls]
    assert names == [
        "claim_agent_runtime_scheduled_wecom_delivery_v2",
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        "start_agent_runtime_scheduled_wecom_dispatch_v2",
        "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
        "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
    ]
    outcome_params = database.calls[-1][1]
    assert outcome_params["p_expected_delivery_state_version"] == 9
    assert outcome_params["p_expected_item_state_version"] == 5
    assert outcome_params["p_receipt_metadata"] == {"http_status": 200, "wecom_errcode": 0}


@pytest.mark.asyncio
async def test_prepared_recovery_preserves_current_claim_and_provider_fences() -> None:
    recovery = {
        **_attempt("readback", "prepared", 12, 6),
        "outcome": "recovered", "intent_id": INTENT,
        "claim_request_id": REQUEST, "worker_id": "recovery-worker",
        "lease_token": LEASE, "lease_expires_at": NOW,
        "prepared_delivery_state_version": 10,
        "prepared_item_state_version": 0,
    }
    database = _Database({
        "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1": recovery,
    })
    result = await PostgresScheduledWecomDeliveryRepository(database).recover_prepared(
        request_id=REQUEST, worker_id="recovery-worker", lease_seconds=90,
    )
    assert result is not None
    assert result.attempt.fence.delivery_state_version == 12
    assert result.attempt.payload_versions == DispatchPayloadVersions(
        delivery_state_version=10, item_state_version=0,
    )
    assert result.attempt.fence.item_state_version == 6
    assert database.calls == [(
        "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        {"p_recovery_request_id": REQUEST, "p_worker_id": "recovery-worker", "p_lease_seconds": 90},
    )]


@pytest.mark.asyncio
async def test_reconcile_claim_renew_read_and_both_result_rpcs_are_exact() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_reconcile_v1": _reconcile(),
        "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1": _reconcile("renewed"),
        "read_agent_runtime_scheduled_wecom_reconcile_v1": _reconcile("readback"),
        "record_agent_runtime_scheduled_wecom_reconcile_result_v1": _reconcile_result(),
        "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1": _reconcile_result(
            "accepted",
        ),
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_reconcile(
        request_id=RECONCILE_REQUEST, worker_id="reconciler",
    )
    assert claim is not None
    assert claim.org_id == ORG
    renewed = await repository.renew_reconcile(claim, lease_seconds=90)
    assert renewed.delivery_state_version == 12
    readback = await repository.read_reconcile(claim)
    assert readback is not None
    still = await repository.record_still_unknown(
        claim, request_id=RESULT_REQUEST, evidence=_evidence(), delay_seconds=300,
    )
    definitive = await repository.record_definitive(
        claim, request_id=RESULT_REQUEST, result=ReconcileResult.ACCEPTED,
        evidence=_evidence(),
    )
    assert still.reconcile_result is ReconcileResult.STILL_UNKNOWN
    assert definitive.reconcile_result is ReconcileResult.ACCEPTED
    still_params = database.calls[-2][1]
    assert still_params["p_reconcile_token"] == RECONCILE_TOKEN
    assert still_params["p_expected_delivery_state_version"] == 11
    assert still_params["p_expected_item_state_version"] == 7
    assert still_params["p_delay_seconds"] == 300
    assert database.calls[-1][0].endswith("reconcile_definitive_result_v1")


@pytest.mark.asyncio
async def test_response_loss_replays_the_exact_request_once() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": [
            OperationalError("lost"), _claim("readback"),
        ],
    })
    claim = await PostgresScheduledWecomDeliveryRepository(database).claim_delivery(
        request_id=REQUEST, worker_id="wecom-worker", lease_seconds=75,
    )
    assert claim is not None
    assert len(database.calls) == 2
    assert database.calls[0] == database.calls[1]


@pytest.mark.asyncio
async def test_lost_outcome_response_replays_exact_versions_and_receipt() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": _claim(),
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2": _attempt("prepared", "prepared", 8, 4),
        "start_agent_runtime_scheduled_wecom_dispatch_v2": _attempt(
            "dispatch_started", "dispatch_started", 9, 5,
        ),
        "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1": [
            OperationalError("lost"), _outcome("readback"),
        ],
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_delivery(request_id=REQUEST, worker_id="wecom-worker")
    assert claim is not None
    started = await repository.start_dispatch(
        await repository.prepare_dispatch(claim, _identity()),
    )
    receipt = await repository.record_dispatch_outcome(
        started, request_id=RESULT_REQUEST,
        dispatch_outcome=DispatchOutcome.ACCEPTED, evidence=_evidence(),
    )
    assert receipt.outcome.value == "readback"
    assert database.calls[-2] == database.calls[-1]


@pytest.mark.asyncio
async def test_lost_reconcile_record_response_replays_exact_result_request() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_reconcile_v1": _reconcile(),
        "record_agent_runtime_scheduled_wecom_reconcile_result_v1": [
            OperationalError("lost"), _reconcile_result(outcome="readback"),
        ],
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_reconcile(
        request_id=RECONCILE_REQUEST, worker_id="reconciler",
    )
    assert claim is not None

    receipt = await repository.record_still_unknown(
        claim, request_id=RESULT_REQUEST, evidence=_evidence(), delay_seconds=300,
    )

    assert receipt.outcome.value == "readback"
    assert database.calls[-2] == database.calls[-1]


@pytest.mark.asyncio
async def test_non_connection_database_error_is_not_replayed() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": RuntimeError("db-contract"),
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    with pytest.raises(RuntimeError, match="db-contract"):
        await repository.claim_delivery(request_id=REQUEST, worker_id="wecom-worker")
    assert len(database.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ("missing", "secret", "status", "type"))
async def test_malformed_or_sensitive_receipts_fail_closed(mutation: str) -> None:
    response = _attempt("prepared", "prepared", 8, 4)
    if mutation == "missing":
        response.pop("item_state_version")
    elif mutation == "secret":
        response["access_token"] = "forbidden"
    elif mutation == "status":
        response["status"] = "retrying"
    else:
        response["delivery_state_version"] = "8"
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": _claim(),
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2": response,
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_delivery(request_id=REQUEST, worker_id="wecom-worker")
    assert claim is not None
    with pytest.raises(PersistenceContractError, match="SCHEDULED_WECOM_RPC_CONTRACT_INVALID"):
        await repository.prepare_dispatch(claim, _identity())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "status"),
    tuple(
        ("prepared", status)
        for status in ("dispatch_started", "accepted", "rejected", "unknown")
    ) + tuple(
        ("dispatch_started", status)
        for status in ("prepared", "accepted", "rejected", "unknown")
    ),
)
async def test_attempt_outcome_status_mismatch_fails_closed(
    outcome: str, status: str,
) -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": _claim(),
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2": _attempt(
            outcome, status, 8, 4,
        ),
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_delivery(request_id=REQUEST, worker_id="wecom-worker")
    assert claim is not None

    with pytest.raises(
        PersistenceContractError,
        match="SCHEDULED_WECOM_RPC_CONTRACT_INVALID:dispatch_attempt_outcome_status",
    ):
        await repository.prepare_dispatch(claim, _identity())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "outcome", "status"),
    (
        ("prepare", "dispatch_started", "dispatch_started"),
        ("start", "prepared", "prepared"),
        ("start", "readback", "prepared"),
        ("read", "prepared", "prepared"),
        ("read", "dispatch_started", "dispatch_started"),
        ("prepare", "readback", "unknown"),
        ("start", "readback", "unknown"),
        ("read", "readback", "unknown"),
    ),
)
async def test_attempt_operation_matrix_fails_closed(
    operation: str, outcome: str, status: str,
) -> None:
    responses = {
        "claim_agent_runtime_scheduled_wecom_delivery_v2": _claim(),
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2": _attempt(
            "prepared", "prepared", 8, 4,
        ),
    }
    rpc_name = {
        "prepare": "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        "start": "start_agent_runtime_scheduled_wecom_dispatch_v2",
        "read": "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
    }[operation]
    responses[rpc_name] = _attempt(outcome, status, 9, 5)
    repository = PostgresScheduledWecomDeliveryRepository(_Database(responses))
    claim = await repository.claim_delivery(request_id=REQUEST, worker_id="wecom-worker")
    assert claim is not None

    with pytest.raises(
        PersistenceContractError,
        match="SCHEDULED_WECOM_RPC_CONTRACT_INVALID:dispatch_attempt_outcome_status",
    ):
        attempt = await repository.prepare_dispatch(claim, _identity())
        if operation == "start":
            await repository.start_dispatch(attempt)
        elif operation == "read":
            await repository.read_attempt(attempt)


@pytest.mark.asyncio
async def test_fenced_response_is_stable_error() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": _claim(),
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2": {"outcome": "fenced"},
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_delivery(request_id=REQUEST, worker_id="wecom-worker")
    assert claim is not None
    with pytest.raises(PersistenceContractError, match="dispatch_attempt_fenced"):
        await repository.prepare_dispatch(claim, _identity())


@pytest.mark.asyncio
async def test_outbound_receipt_rejects_unknown_evidence_and_free_text_metadata() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_wecom_delivery_v2": _claim(),
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2": _attempt("prepared", "prepared", 8, 4),
        "start_agent_runtime_scheduled_wecom_dispatch_v2": _attempt(
            "dispatch_started", "dispatch_started", 9, 5,
        ),
    })
    repository = PostgresScheduledWecomDeliveryRepository(database)
    claim = await repository.claim_delivery(request_id=REQUEST, worker_id="wecom-worker")
    assert claim is not None
    started = await repository.start_dispatch(
        await repository.prepare_dispatch(claim, _identity()),
    )
    with pytest.raises(PersistenceContractError, match="unknown_evidence_forbidden"):
        await repository.record_dispatch_outcome(
            started, request_id=RESULT_REQUEST,
            dispatch_outcome=DispatchOutcome.UNKNOWN, evidence=_evidence(),
        )
    free_text = ProviderReceiptEvidence(
        receipt_type=ReceiptType.WECOM_APP, receipt_hash=HASH,
        receipt_code="ok",
        metadata=ReceiptMetadata(provider_message_id="spaces are forbidden"),
    )
    with pytest.raises(PersistenceContractError, match="RPC_CONTRACT_INVALID"):
        await repository.record_dispatch_outcome(
            started, request_id=RESULT_REQUEST,
            dispatch_outcome=DispatchOutcome.ACCEPTED, evidence=free_text,
        )
    assert all("record_agent_runtime" not in name for name, _ in database.calls)


def test_repository_requires_existing_wecom_worker_scope() -> None:
    with pytest.raises(ValueError, match="WECOM_WORKER_SCOPED"):
        PostgresScheduledWecomDeliveryRepository(
            _Database({}, DatabaseAccessKind.AGENT_RUNTIME),
        )
