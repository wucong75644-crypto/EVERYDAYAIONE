"""Typed Python boundary for the 227_48 started-dispatch recovery RPC."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.scheduled_wecom_delivery import (
    PostgresScheduledWecomDeliveryRepository,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptStatus,
    DeliveryStatus,
    DispatchOutcome,
    DispatchPhase,
    ItemStatus,
    StartedRecoveryOutcome,
)


REQUEST = "11111111-1111-1111-1111-111111111111"
ORG = "22222222-2222-2222-2222-222222222222"
INTENT = "33333333-3333-3333-3333-333333333333"
ITEM = "44444444-4444-4444-4444-444444444444"
ATTEMPT = "55555555-5555-5555-5555-555555555555"
OUTCOME_REQUEST = "66666666-6666-6666-6666-666666666666"
WORKER = "started-recovery-worker"
NOW = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)


def _recovery(outcome: str = "recovered") -> dict[str, object]:
    return {
        "outcome": outcome,
        "request_id": REQUEST,
        "recovery_worker_id": WORKER,
        "org_id": ORG,
        "intent_id": INTENT,
        "item_id": ITEM,
        "attempt_id": ATTEMPT,
        "outcome_request_id": OUTCOME_REQUEST,
        "dispatch_outcome": "unknown",
        "attempt_status": "unknown",
        "dispatch_phase": "ambiguous",
        "item_status": "unknown",
        "delivery_status": "unknown",
        "delivery_state_version": 12,
        "item_state_version": 8,
        "recovered_at": NOW,
    }


class _Response:
    def __init__(self, data: object) -> None:
        self.data = data


class _Call:
    def __init__(self, database: _Database, name: str, params: dict[str, object]) -> None:
        self.database = database
        self.name = name
        self.params = params

    async def execute(self) -> _Response:
        self.database.calls.append((self.name, self.params))
        values = self.database.responses[self.name]
        value = values.pop(0) if isinstance(values, list) else values
        if isinstance(value, BaseException):
            raise value
        return _Response(deepcopy(value))


class _Database:
    def __init__(self, response: object) -> None:
        self.scope = DatabaseScope(
            None, None, DatabaseAccessKind.WORKER, "started-recovery-test",
        )
        self.responses = {
            "recover_agent_runtime_scheduled_wecom_started_dispatch_v1": response,
        }
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]) -> _Call:
        return _Call(self, name, params)

    def table(self, _name: str) -> None:
        raise AssertionError("business-table access is forbidden")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ("recovered", "readback"))
async def test_started_recovery_parses_exact_unknown_receipt(outcome: str) -> None:
    database = _Database(_recovery(outcome))
    result = await PostgresScheduledWecomDeliveryRepository(database).recover_started(
        request_id=REQUEST, worker_id=WORKER,
    )

    assert result is not None
    assert result.outcome is StartedRecoveryOutcome(outcome)
    assert result.request_id == REQUEST
    assert result.recovery_worker_id == WORKER
    assert (result.org_id, result.intent_id, result.item_id, result.attempt_id) == (
        ORG, INTENT, ITEM, ATTEMPT,
    )
    assert result.outcome_request_id == OUTCOME_REQUEST
    assert result.dispatch_outcome is DispatchOutcome.UNKNOWN
    assert result.attempt_status is AttemptStatus.UNKNOWN
    assert result.dispatch_phase is DispatchPhase.AMBIGUOUS
    assert result.item_status is ItemStatus.UNKNOWN
    assert result.delivery_status is DeliveryStatus.UNKNOWN
    assert (result.delivery_state_version, result.item_state_version) == (12, 8)
    assert result.recovered_at == NOW
    assert database.calls == [(
        "recover_agent_runtime_scheduled_wecom_started_dispatch_v1",
        {"p_request_id": REQUEST, "p_recovery_worker_id": WORKER},
    )]


@pytest.mark.asyncio
async def test_started_recovery_empty_is_none() -> None:
    result = await PostgresScheduledWecomDeliveryRepository(
        _Database({"outcome": "empty"}),
    ).recover_started(request_id=REQUEST, worker_id=WORKER)
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_id", ("", " ", f" {WORKER}", f"{WORKER} ", "w" * 129))
async def test_started_recovery_rejects_invalid_worker_before_rpc(
    worker_id: str,
) -> None:
    database = _Database(_recovery())
    with pytest.raises(
        PersistenceContractError,
        match="SCHEDULED_WECOM_STARTED_RECOVERY_CONTRACT_INVALID:recovery_worker_id",
    ):
        await PostgresScheduledWecomDeliveryRepository(database).recover_started(
            request_id=REQUEST, worker_id=worker_id,
        )
    assert database.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_id",
    ("", "not-a-uuid", f" {REQUEST}", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
)
async def test_started_recovery_rejects_noncanonical_request_before_rpc(
    request_id: str,
) -> None:
    database = _Database(_recovery())
    with pytest.raises(
        PersistenceContractError,
        match="SCHEDULED_WECOM_STARTED_RECOVERY_CONTRACT_INVALID:request_id",
    ):
        await PostgresScheduledWecomDeliveryRepository(database).recover_started(
            request_id=request_id, worker_id=WORKER,
        )
    assert database.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("unknown_field", "forbidden"),
        ("delivery_state_version", "12"),
        ("item_state_version", True),
        ("outcome", "completed"),
        ("request_id", OUTCOME_REQUEST),
        ("recovery_worker_id", "other-worker"),
        ("outcome_request_id", REQUEST),
        ("dispatch_outcome", "accepted"),
        ("attempt_status", "dispatch_started"),
        ("dispatch_phase", "external_request_started"),
        ("item_status", "accepted"),
        ("delivery_status", "completed"),
    ),
)
async def test_started_recovery_rejects_field_drift_and_wrong_types(
    mutation: str, value: object,
) -> None:
    response = _recovery()
    response[mutation] = value
    repository = PostgresScheduledWecomDeliveryRepository(_Database(response))

    with pytest.raises(
        PersistenceContractError,
        match="SCHEDULED_WECOM_STARTED_RECOVERY_CONTRACT_INVALID",
    ):
        await repository.recover_started(request_id=REQUEST, worker_id=WORKER)


@pytest.mark.asyncio
async def test_started_recovery_rejects_missing_field() -> None:
    response = _recovery()
    response.pop("attempt_id")
    with pytest.raises(PersistenceContractError, match="CONTRACT_INVALID"):
        await PostgresScheduledWecomDeliveryRepository(
            _Database(response),
        ).recover_started(request_id=REQUEST, worker_id=WORKER)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ("fenced", "not_found", "unavailable"))
async def test_started_recovery_typed_failures_fail_closed(outcome: str) -> None:
    repository = PostgresScheduledWecomDeliveryRepository(
        _Database({"outcome": outcome}),
    )
    with pytest.raises(PersistenceContractError, match=f"started_recovery_{outcome}"):
        await repository.recover_started(request_id=REQUEST, worker_id=WORKER)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", (OperationalError("lost"), InterfaceError("lost")))
async def test_started_recovery_response_loss_replays_same_request(error: Exception) -> None:
    database = _Database([error, _recovery("readback")])
    result = await PostgresScheduledWecomDeliveryRepository(database).recover_started(
        request_id=REQUEST, worker_id=WORKER,
    )
    assert result is not None and result.outcome is StartedRecoveryOutcome.READBACK
    assert len(database.calls) == 2
    assert database.calls[0] == database.calls[1]


@pytest.mark.asyncio
async def test_started_recovery_propagates_cancelled_error_without_replay() -> None:
    database = _Database(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await PostgresScheduledWecomDeliveryRepository(database).recover_started(
            request_id=REQUEST, worker_id=WORKER,
        )
    assert len(database.calls) == 1
