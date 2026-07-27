"""Typed ModelAttempt PostgreSQL adapter tests."""

from typing import Any

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.model_attempt_parsing import (
    parse_attempt_receipt,
)
from services.agent.runtime.infrastructure.postgres.model_attempt_repository import (
    PostgresModelAttemptRepository,
)
from services.agent.runtime.ports.model_attempt import ModelAttemptOutcome


class Response:
    def __init__(self, data: object) -> None:
        self.data = data


class Call:
    def __init__(self, database: "Database", name: str, params: dict) -> None:
        self.database = database
        self.name = name
        self.params = params

    async def execute(self) -> Response:
        self.database.calls.append((self.name, self.params))
        return Response(self.database.responses[self.name])


class Database:
    def __init__(self, responses: dict[str, object]) -> None:
        self.scope = DatabaseScope(
            actor_user_id=None, org_id=None,
            access_kind=DatabaseAccessKind.WORKER,
            request_id="ar11-test",
        )
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict) -> Call:
        return Call(self, name, params)


def test_unknown_rpc_outcome_fails_closed() -> None:
    with pytest.raises(PersistenceContractError):
        parse_attempt_receipt(
            {"outcome": "future_success"},
            {ModelAttemptOutcome.PREPARED},
        )


@pytest.mark.asyncio
async def test_tool_calls_return_typed_handoff() -> None:
    database = Database({
        "complete_model_attempt_without_actions": {
            "outcome": "handoff_tool_calls",
        },
    })
    repository = PostgresModelAttemptRepository(database)

    receipt = await repository.complete_without_actions(
        attempt_id="11111111-1111-1111-1111-111111111111",
        run_execution_token="22222222-2222-2222-2222-222222222222",
        expected_attempt_version=1,
        expected_step_version=0,
        request_hash="a" * 64,
        response_receipt={},
        response_hash="b" * 64,
        stop_reason="tool_calls",
        provider_stop_reason="tool_calls",
        usage={},
        actual_credits=0,
    )

    assert receipt.outcome is ModelAttemptOutcome.HANDOFF_TOOL_CALLS
    assert database.calls[0][0] == "complete_model_attempt_without_actions"


def test_runtime_scope_cannot_construct_worker_repository() -> None:
    database = Database({})
    database.scope = DatabaseScope(
        actor_user_id="33333333-3333-3333-3333-333333333333",
        org_id=None, access_kind=DatabaseAccessKind.RUNTIME,
        request_id="runtime",
    )
    with pytest.raises(ValueError, match="WORKER_SCOPED"):
        PostgresModelAttemptRepository(database)
