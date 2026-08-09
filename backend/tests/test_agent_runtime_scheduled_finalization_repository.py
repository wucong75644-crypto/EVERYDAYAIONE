"""Strict RPC parsing and lost-response recovery for scheduled finalization."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from psycopg import OperationalError

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.scheduled_finalization_repository import (
    PostgresScheduledFinalizationRepository,
)
from services.agent.runtime.ports.scheduled_finalization import (
    ScheduledFinalizationProjection,
    ScheduledFinalizationOutcome,
)


RUN_ID = "11111111-1111-1111-1111-111111111111"
TASK_ID = "22222222-2222-2222-2222-222222222222"
TOKEN = "33333333-3333-3333-3333-333333333333"
BASELINE = datetime(2026, 8, 10, 1, 2, tzinfo=timezone.utc)


class _Response:
    def __init__(self, data): self.data = data


class _Call:
    def __init__(self, database, name, params):
        self.database, self.name, self.params = database, name, params

    async def execute(self):
        self.database.calls.append((self.name, self.params))
        values = self.database.responses[self.name]
        value = values.pop(0) if isinstance(values, list) else values
        if isinstance(value, BaseException):
            raise value
        return _Response(value)


class _Database:
    def __init__(self, responses, access=DatabaseAccessKind.AGENT_RUNTIME):
        self.scope = DatabaseScope(None, None, access, "b1-b2-test")
        self.responses = responses
        self.calls = []

    def rpc(self, name, params): return _Call(self, name, params)


def _claimed():
    return {"outcome": "claimed", "intent": {
        "scheduled_run_id": RUN_ID,
        "claim_token": TOKEN,
        "state_version": 4,
        "claim_lease_expires_at": datetime(
            2026, 8, 10, 2, tzinfo=timezone.utc,
        ),
    }}


def _context():
    return {"outcome": "found", "context": {
        "scheduled_run_id": RUN_ID,
        "terminal_status": "completed",
        "terminal_baseline": BASELINE,
        "intent_state_version": 4,
        "task_state_version": 7,
        "schedule_hash": "a" * 64,
        "schedule_type": "daily",
        "cron_expr": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "retry_count": 1,
        "consecutive_failures": 0,
    }}


def _applied(outcome="applied"):
    return {
        "outcome": outcome,
        "scheduled_run_id": RUN_ID,
        "scheduled_task_id": TASK_ID,
        "terminal_status": "completed",
        "scheduled_run_status": "success",
        "task_status": "active",
        "task_state_version": 8,
    }


@pytest.mark.asyncio
async def test_claim_context_and_apply_use_only_narrow_v2_surface() -> None:
    database = _Database({
        "claim_next_agent_runtime_scheduled_finalization_v1": _claimed(),
        "read_agent_runtime_scheduled_finalization_context_v1": _context(),
        "apply_agent_runtime_scheduled_finalization_v2": _applied(),
    })
    repository = PostgresScheduledFinalizationRepository(database)
    claim = await repository.claim_next("worker-1")
    assert claim is not None
    context = await repository.read_context(claim)
    receipt = await repository.apply(claim, context, ScheduledFinalizationProjection(
        request_id="44444444-4444-4444-4444-444444444444",
        next_run_at=datetime(2026, 8, 11, 1, tzinfo=timezone.utc),
    ))
    assert receipt.outcome is ScheduledFinalizationOutcome.APPLIED
    assert [name for name, _ in database.calls] == [
        "claim_next_agent_runtime_scheduled_finalization_v1",
        "read_agent_runtime_scheduled_finalization_context_v1",
        "apply_agent_runtime_scheduled_finalization_v2",
    ]


@pytest.mark.asyncio
async def test_lost_apply_response_requires_readback_then_exact_replay() -> None:
    database = _Database({
        "claim_next_agent_runtime_scheduled_finalization_v1": _claimed(),
        "read_agent_runtime_scheduled_finalization_context_v1": _context(),
        "apply_agent_runtime_scheduled_finalization_v2": [
            OperationalError("lost"), _applied("already_applied"),
        ],
        "read_agent_runtime_scheduled_finalization_v1": {
            "outcome": "found", "intent": {"status": "applied"},
        },
    })
    repository = PostgresScheduledFinalizationRepository(database)
    claim = await repository.claim_next("worker-1")
    assert claim is not None
    context = await repository.read_context(claim)
    receipt = await repository.apply(claim, context, ScheduledFinalizationProjection(
        request_id="44444444-4444-4444-4444-444444444444",
        next_run_at=datetime(2026, 8, 11, 1, tzinfo=timezone.utc),
    ))
    assert receipt.outcome is ScheduledFinalizationOutcome.ALREADY_APPLIED
    apply_calls = [params for name, params in database.calls if name.startswith("apply_")]
    assert len(apply_calls) == 2 and apply_calls[0] == apply_calls[1]


@pytest.mark.asyncio
async def test_unconfirmed_lost_response_remains_recoverable() -> None:
    database = _Database({
        "claim_next_agent_runtime_scheduled_finalization_v1": _claimed(),
        "read_agent_runtime_scheduled_finalization_context_v1": _context(),
        "apply_agent_runtime_scheduled_finalization_v2": OperationalError("lost"),
        "read_agent_runtime_scheduled_finalization_v1": {
            "outcome": "found", "intent": {"status": "claimed"},
        },
    })
    repository = PostgresScheduledFinalizationRepository(database)
    claim = await repository.claim_next("worker-1")
    assert claim is not None
    context = await repository.read_context(claim)
    with pytest.raises(OperationalError, match="APPLY_UNCONFIRMED"):
        await repository.apply(claim, context, ScheduledFinalizationProjection(
            request_id="44444444-4444-4444-4444-444444444444",
            next_run_at=datetime(2026, 8, 11, 1, tzinfo=timezone.utc),
        ))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"outcome": "claimed", "intent": {}},
    {"outcome": "claimed", "intent": {
        "scheduled_run_id": "bad", "claim_token": TOKEN,
        "state_version": 4, "claim_lease_expires_at": BASELINE,
    }},
])
async def test_malformed_claim_fails_closed(bad) -> None:
    repository = PostgresScheduledFinalizationRepository(_Database({
        "claim_next_agent_runtime_scheduled_finalization_v1": bad,
    }))
    with pytest.raises(PersistenceContractError):
        await repository.claim_next("worker-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    (
        ("terminal_status", "accepted"),
        ("schedule_hash", "bad"),
        ("scheduled_run_id", TASK_ID),
        ("intent_state_version", 5),
        ("cron_expr", None),
    ),
)
async def test_malformed_or_changed_context_fails_closed(field, value) -> None:
    context = _context()
    context["context"][field] = value
    database = _Database({
        "claim_next_agent_runtime_scheduled_finalization_v1": _claimed(),
        "read_agent_runtime_scheduled_finalization_context_v1": context,
    })
    repository = PostgresScheduledFinalizationRepository(database)
    claim = await repository.claim_next("worker-1")
    assert claim is not None
    with pytest.raises(PersistenceContractError):
        await repository.read_context(claim)


@pytest.mark.asyncio
async def test_apply_receipt_identity_mismatch_fails_closed() -> None:
    wrong = _applied()
    wrong["scheduled_run_id"] = TASK_ID
    database = _Database({
        "claim_next_agent_runtime_scheduled_finalization_v1": _claimed(),
        "read_agent_runtime_scheduled_finalization_context_v1": _context(),
        "apply_agent_runtime_scheduled_finalization_v2": wrong,
    })
    repository = PostgresScheduledFinalizationRepository(database)
    claim = await repository.claim_next("worker-1")
    assert claim is not None
    context = await repository.read_context(claim)
    with pytest.raises(PersistenceContractError, match="changed identity"):
        await repository.apply(claim, context, ScheduledFinalizationProjection(
            request_id="44444444-4444-4444-4444-444444444444",
            next_run_at=datetime(2026, 8, 11, 1, tzinfo=timezone.utc),
        ))


def test_repository_rejects_non_runtime_scope() -> None:
    with pytest.raises(ValueError, match="WORKER_SCOPED"):
        PostgresScheduledFinalizationRepository(
            _Database({}, DatabaseAccessKind.RUNTIME),
        )
