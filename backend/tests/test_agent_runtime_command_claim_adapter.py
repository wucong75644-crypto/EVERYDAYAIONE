"""Typed adapter contract for AR-13 Command claims."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from psycopg import OperationalError

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.command_claim_repository import (
    PostgresCommandClaimRepository,
)
from services.agent.runtime.ports.command_claim import CommandClaimOutcome


COMMAND_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
TOKEN = "44444444-4444-4444-4444-444444444444"


class _Response:
    def __init__(self, data: object) -> None:
        self.data = data


class _Call:
    def __init__(self, database: "_Database", name: str) -> None:
        self._database = database
        self._name = name

    async def execute(self) -> _Response:
        self._database.calls.append(self._name)
        value = self._database.responses[self._name]
        if isinstance(value, tuple):
            current, *rest = value
            self._database.responses[self._name] = tuple(rest)
            value = current
        if isinstance(value, BaseException):
            raise value
        return _Response(value)


class _Database:
    def __init__(
        self, access: DatabaseAccessKind, responses: dict[str, object],
    ) -> None:
        self.scope = DatabaseScope(None, None, access, "ar13-test")
        self.responses = responses
        self.calls: list[str] = []

    def rpc(self, name: str, _params: dict[str, object]) -> _Call:
        return _Call(self, name)


def _claim(outcome: str = "claimed") -> dict[str, object]:
    return {
        "outcome": outcome,
        "command_id": COMMAND_ID,
        "session_id": SESSION_ID,
        "run_id": RUN_ID,
        "worker_id": "worker-1",
        "fencing_token": TOKEN,
        "lease_expires_at": datetime(2026, 7, 27, tzinfo=timezone.utc),
        "attempt_number": 1,
        "command_type": "submit_input",
    }


def test_only_worker_scoped_client_is_accepted() -> None:
    with pytest.raises(ValueError, match="WORKER_DATABASE_SCOPE_REQUIRED"):
        PostgresCommandClaimRepository(
            _Database(DatabaseAccessKind.RUNTIME, {}),
        )


@pytest.mark.asyncio
async def test_claim_parses_strict_typed_receipt() -> None:
    repository = PostgresCommandClaimRepository(_Database(
        DatabaseAccessKind.AGENT_RUNTIME,
        {"claim_pending_agent_command_and_ensure_run": _claim()},
    ))

    receipt = await repository.claim_next("worker-1")

    assert receipt.outcome is CommandClaimOutcome.CLAIMED
    assert receipt.claim is not None
    assert receipt.claim.command_id == COMMAND_ID
    assert receipt.claim.attempt_number == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["already_processed", "association_rejected"],
)
async def test_non_active_eligibility_receipts_have_no_claim(
    outcome: str,
) -> None:
    repository = PostgresCommandClaimRepository(_Database(
        DatabaseAccessKind.AGENT_RUNTIME,
        {"claim_pending_agent_command_and_ensure_run": {
            "outcome": outcome,
            "command_id": COMMAND_ID,
            "run_id": RUN_ID,
        }},
    ))

    receipt = await repository.claim_next("worker-1")

    assert receipt.outcome.value == outcome
    assert receipt.claim is None


@pytest.mark.asyncio
async def test_unknown_or_incomplete_receipt_fails_closed() -> None:
    for response in ({"outcome": "future"}, {"outcome": "claimed"}):
        repository = PostgresCommandClaimRepository(_Database(
            DatabaseAccessKind.AGENT_RUNTIME,
            {"claim_pending_agent_command_and_ensure_run": response},
        ))
        with pytest.raises(PersistenceContractError):
            await repository.claim_next("worker-1")


@pytest.mark.asyncio
async def test_uncertain_renewal_reads_back_by_command_and_worker() -> None:
    database = _Database(DatabaseAccessKind.AGENT_RUNTIME, {
        "renew_agent_command_claim": OperationalError("response lost"),
        "get_agent_command_run_claim": _claim("found"),
    })
    repository = PostgresCommandClaimRepository(database)
    claimed = (await PostgresCommandClaimRepository(_Database(
        DatabaseAccessKind.AGENT_RUNTIME,
        {"claim_pending_agent_command_and_ensure_run": _claim()},
    )).claim_next("worker-1")).claim

    assert claimed is not None
    recovered = await repository.renew(claimed)

    assert recovered.outcome is CommandClaimOutcome.FOUND
    assert database.calls == [
        "renew_agent_command_claim",
        "get_agent_command_run_claim",
    ]


@pytest.mark.asyncio
async def test_uncertain_claim_discovers_then_reads_exact_command() -> None:
    database = _Database(DatabaseAccessKind.AGENT_RUNTIME, {
        "claim_pending_agent_command_and_ensure_run": OperationalError(
            "response lost",
        ),
        "get_agent_command_run_claim": (
            _claim("found"),
            _claim("found"),
        ),
    })

    recovered = await PostgresCommandClaimRepository(database).claim_next(
        "worker-1",
    )

    assert recovered.claim is not None
    assert recovered.claim.command_id == COMMAND_ID
    assert database.calls == [
        "claim_pending_agent_command_and_ensure_run",
        "get_agent_command_run_claim",
        "get_agent_command_run_claim",
    ]
