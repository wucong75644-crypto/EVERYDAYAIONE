"""Typed and scoped contracts for Agent Runtime PostgreSQL adapters."""

from __future__ import annotations

from datetime import datetime, timezone
import asyncio
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain import (
    EventSequence,
    FencingToken,
    RunId,
    SessionId,
)
from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    PersistenceContractError,
    StaleVersionError,
    TerminalConflictError,
)
from services.agent.runtime.infrastructure.postgres.event_store import (
    PostgresRuntimeEventStore,
)
from services.agent.runtime.infrastructure.postgres.parsing import (
    mutation_receipt,
)
from services.agent.runtime.infrastructure.postgres.projection_outbox import (
    PostgresProjectionOutbox,
)
from services.agent.runtime.infrastructure.postgres.repository import (
    PostgresRuntimeRepository,
)
from services.agent.runtime.ports.repository import (
    ClaimOutcome,
    MutationOutcome,
)


SESSION_ID = "11111111-1111-1111-1111-111111111111"
CONVERSATION_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
EVENT_ID = "44444444-4444-4444-4444-444444444444"
TOKEN = "55555555-5555-5555-5555-555555555555"
USER_ID = "66666666-6666-6666-6666-666666666666"
OUTBOX_ID = "77777777-7777-7777-7777-777777777777"


class _Response:
    def __init__(self, data: object):
        self.data = data


class _Call:
    def __init__(self, database: "_Database", name: str, params: dict):
        self._database = database
        self._name = name
        self._params = params

    async def execute(self) -> _Response:
        self._database.calls.append((self._name, self._params))
        values = self._database.responses[self._name]
        value = values[0] if isinstance(values, tuple) else values
        if isinstance(values, tuple):
            self._database.responses[self._name] = values[1:]
        if isinstance(value, BaseException):
            raise value
        return _Response(value)


class _Database:
    def __init__(
        self, access_kind: DatabaseAccessKind,
        responses: dict[str, object],
    ):
        self.scope = DatabaseScope(
            actor_user_id=(
                USER_ID if access_kind is DatabaseAccessKind.RUNTIME else None
            ),
            org_id=None,
            access_kind=access_kind,
            request_id="ar08-test",
        )
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict) -> _Call:
        return _Call(self, name, params)


def _event(sequence: int = 1) -> dict[str, object]:
    return {
        "id": EVENT_ID,
        "session_id": SESSION_ID,
        "sequence": sequence,
        "org_id": None,
        "user_id": USER_ID,
        "scope_kind": "user",
        "scope_id": USER_ID,
        "event_type": "run.completed",
        "event_version": 1,
        "durability": "durable",
        "run_id": RUN_ID,
        "model_step_id": None,
        "action_id": None,
        "causation_event_id": None,
        "correlation_id": TOKEN,
        "actor_type": "system",
        "actor_id": "worker",
        "payload": {},
        "payload_hash": "hash",
        "occurred_at": "2026-07-27T10:00:00+00:00",
        "redaction_revision": "v1",
        "trace_id": None,
        "span_id": None,
    }


def test_unknown_outcome_and_invalid_uuid_fail_closed() -> None:
    with pytest.raises(PersistenceContractError, match="unknown RPC outcome"):
        mutation_receipt(
            {"outcome": "future_success"},
            {MutationOutcome.CREATED},
        )
    with pytest.raises(PersistenceContractError, match="UUID required"):
        mutation_receipt(
            {"outcome": "created", "entity_id": "not-a-uuid"},
            {MutationOutcome.CREATED},
        )


def test_ownership_loss_maps_to_domain_error() -> None:
    with pytest.raises(FencingTokenMismatchError):
        mutation_receipt(
            {"outcome": "ownership_lost"},
            {MutationOutcome.COMPLETED},
        )


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    [
        ("scope_kind", "future", "unknown ScopeKind"),
        ("occurred_at", "not-a-time", "timestamp required"),
        ("payload", [], "JSON object required"),
    ],
)
def test_event_envelope_rejects_invalid_enum_time_and_json(
    field: str, invalid: object, message: str,
) -> None:
    from services.agent.runtime.infrastructure.postgres.event_store import (
        event_from_row,
    )

    row = deepcopy(_event())
    row[field] = invalid
    with pytest.raises(PersistenceContractError, match=message):
        event_from_row(row)


@pytest.mark.asyncio
async def test_repository_claim_reads_complete_attempt_through_rpc() -> None:
    database = _Database(DatabaseAccessKind.WORKER, {
        "claim_agent_run": {
            "outcome": "claimed",
            "entity_id": RUN_ID,
            "execution_token": TOKEN,
            "state_version": 1,
            "event_sequence": 3,
        },
        "get_agent_runtime_run_claim": {
            "outcome": "found",
            "state_version": 1,
            "event_sequence": 3,
            "attempt": {
                "run_id": RUN_ID,
                "org_id": None,
                "user_id": USER_ID,
                "scope_kind": "user",
                "scope_id": USER_ID,
                "attempt_number": 1,
                "worker_id": "worker-1",
                "execution_token": TOKEN,
                "claimed_at": "2026-07-27T10:00:00+00:00",
                "lease_expires_at": "2026-07-27T10:01:30+00:00",
            },
        },
    })

    claim = await PostgresRuntimeRepository(database).claim_run(
        RunId(RUN_ID), "worker-1",
    )

    assert claim.outcome is ClaimOutcome.CLAIMED
    assert claim.attempt is not None
    assert claim.attempt.lease.fencing_token == TOKEN
    assert [name for name, _ in database.calls] == [
        "claim_agent_run", "get_agent_runtime_run_claim",
    ]


@pytest.mark.asyncio
async def test_claim_recovers_after_committed_response_disconnect() -> None:
    from psycopg import OperationalError

    attempt = {
        "outcome": "found",
        "state_version": 1,
        "event_sequence": 3,
        "attempt": {
            "run_id": RUN_ID, "org_id": None, "user_id": USER_ID,
            "scope_kind": "user", "scope_id": USER_ID,
            "attempt_number": 1, "worker_id": "worker-1",
            "execution_token": TOKEN,
            "claimed_at": "2026-07-27T10:00:00+00:00",
            "lease_expires_at": "2026-07-27T10:01:30+00:00",
        },
    }
    database = _Database(DatabaseAccessKind.WORKER, {
        "claim_agent_run": OperationalError("response lost"),
        "get_agent_runtime_run_claim": attempt,
    })

    claim = await PostgresRuntimeRepository(database).claim_run(
        RunId(RUN_ID), "worker-1",
    )

    assert claim.outcome is ClaimOutcome.CLAIMED
    assert claim.state_version == 1
    assert claim.event_sequence == 3


@pytest.mark.asyncio
async def test_adapter_cancellation_is_not_converted_to_business_outcome() -> None:
    database = _Database(DatabaseAccessKind.WORKER, {
        "claim_agent_run": asyncio.CancelledError(),
    })

    with pytest.raises(asyncio.CancelledError):
        await PostgresRuntimeRepository(database).claim_run(
            RunId(RUN_ID), "worker-1",
        )


@pytest.mark.asyncio
async def test_runtime_scope_cannot_call_worker_repository_operation() -> None:
    repository = PostgresRuntimeRepository(
        _Database(DatabaseAccessKind.RUNTIME, {}),
    )
    with pytest.raises(ValueError, match="WORKER_DATABASE_SCOPE_REQUIRED"):
        await repository.claim_run(RunId(RUN_ID), "worker")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access_kind",
    [DatabaseAccessKind.RUNTIME, DatabaseAccessKind.WORKER],
)
async def test_runtime_and_worker_scopes_can_call_cancel(
    access_kind: DatabaseAccessKind,
) -> None:
    database = _Database(access_kind, {
        "cancel_agent_run": {
            "outcome": "cancelled",
            "entity_id": RUN_ID,
            "state_version": 2,
            "event_sequence": 4,
        },
    })

    receipt = await PostgresRuntimeRepository(database).cancel_run(
        RunId(RUN_ID), 1, "user_cancelled",
    )

    assert receipt.outcome is MutationOutcome.CANCELLED
    assert database.calls[0][0] == "cancel_agent_run"


@pytest.mark.asyncio
async def test_runtime_cancel_preserves_closed_conflict_outcomes() -> None:
    for response, error in (
        ({"outcome": "stale_version"}, StaleVersionError),
        ({"outcome": "terminal_conflict"}, TerminalConflictError),
    ):
        database = _Database(DatabaseAccessKind.RUNTIME, {
            "cancel_agent_run": response,
        })
        with pytest.raises(error):
            await PostgresRuntimeRepository(database).cancel_run(
                RunId(RUN_ID), 1, "user_cancelled",
            )


@pytest.mark.asyncio
async def test_runtime_scope_remains_blocked_from_worker_mutations() -> None:
    repository = PostgresRuntimeRepository(
        _Database(DatabaseAccessKind.RUNTIME, {}),
    )

    with pytest.raises(ValueError, match="WORKER_DATABASE_SCOPE_REQUIRED"):
        await repository.complete_run(
            RunId(RUN_ID), FencingToken(TOKEN), 1, "result",
        )
    with pytest.raises(ValueError, match="WORKER_DATABASE_SCOPE_REQUIRED"):
        await repository.set_run_waiting(
            RunId(RUN_ID), FencingToken(TOKEN), 1, "paused",
        )


@pytest.mark.asyncio
async def test_get_session_returns_typed_scope() -> None:
    database = _Database(DatabaseAccessKind.RUNTIME, {
        "get_agent_runtime_session": {
            "outcome": "found",
            "session": {
                "id": SESSION_ID,
                "conversation_id": CONVERSATION_ID,
                "org_id": None,
                "user_id": USER_ID,
                "scope_kind": "user",
                "scope_id": USER_ID,
                "created_by_user_id": USER_ID,
                "agent_definition_id": "default",
                "agent_definition_revision": "v1",
                "next_event_sequence": 2,
                "state_version": 0,
            },
        },
    })

    session = await PostgresRuntimeRepository(database).get_session(
        SessionId(SESSION_ID),
    )

    assert session is not None
    assert session.session_id == SESSION_ID
    assert session.scope.user_id == USER_ID


@pytest.mark.asyncio
async def test_event_replay_rejects_gap() -> None:
    database = _Database(DatabaseAccessKind.RUNTIME, {
        "replay_agent_runtime_events": {
            "outcome": "found",
            "events": [_event(sequence=2)],
        },
    })
    store = PostgresRuntimeEventStore(database)

    with pytest.raises(PersistenceContractError, match="expected 1"):
        _ = [event async for event in store.replay(SessionId(SESSION_ID))]


@pytest.mark.asyncio
async def test_event_replay_after_sequence_is_contiguous() -> None:
    event = _event(sequence=2)
    database = _Database(DatabaseAccessKind.RUNTIME, {
        "replay_agent_runtime_events": {
            "outcome": "found",
            "events": [event],
        },
    })

    events = [
        item async for item in PostgresRuntimeEventStore(database).replay(
            SessionId(SESSION_ID), EventSequence(1),
        )
    ]

    assert [item.sequence.value for item in events] == [2]
    assert events[0].occurred_at == datetime(
        2026, 7, 27, 10, tzinfo=timezone.utc,
    )


@pytest.mark.asyncio
async def test_projection_claim_contains_complete_event_envelope() -> None:
    outbox = {
        "id": OUTBOX_ID,
        "event_id": EVENT_ID,
        "projection_kind": "web_runtime",
        "lease_token": TOKEN,
        "lease_expires_at": "2026-07-27T10:01:00+00:00",
        "attempt_count": 1,
        "checkpoint": {},
    }
    database = _Database(DatabaseAccessKind.WORKER, {
        "claim_agent_projection_outbox": ([outbox],),
        "get_claimed_agent_projection_event": {
            "outcome": "found",
            "outbox": outbox,
            "event": _event(),
        },
    })

    [claim] = await PostgresProjectionOutbox(database).claim()

    assert claim.outbox_id == str(UUID(OUTBOX_ID))
    assert claim.event.event_id == EVENT_ID
    assert claim.event.payload == {}
