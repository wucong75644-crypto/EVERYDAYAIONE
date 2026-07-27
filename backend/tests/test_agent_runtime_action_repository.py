"""Typed Action PostgreSQL adapter tests."""

from typing import Any

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.action_parsing import (
    parse_action_receipt,
)
from services.agent.runtime.infrastructure.postgres.action_repository import (
    PostgresActionRepository,
)
from services.agent.runtime.ports.action_repository import ActionMutationOutcome


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
            request_id="ar12-test",
        )
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict) -> Call:
        return Call(self, name, params)


def test_unknown_outcome_fails_closed() -> None:
    with pytest.raises(PersistenceContractError):
        parse_action_receipt({"outcome": "future_success"})


@pytest.mark.asyncio
async def test_complete_tool_calls_forwards_complete_batch() -> None:
    database = Database({
        "complete_model_attempt_step_and_create_actions": {
            "outcome": "completed",
            "attempt_id": "11111111-1111-1111-1111-111111111111",
            "model_step_id": "22222222-2222-2222-2222-222222222222",
            "run_id": "33333333-3333-3333-3333-333333333333",
            "run_status": "waiting_actions",
            "blocking_action_count": 1,
            "batch_hash": "a" * 32,
            "action_ids": ["44444444-4444-4444-4444-444444444444"],
        },
    })
    repository = PostgresActionRepository(database)
    receipt = await repository.complete_tool_calls(
        attempt_id="11111111-1111-1111-1111-111111111111",
        run_execution_token="55555555-5555-5555-5555-555555555555",
        expected_attempt_version=1, expected_step_version=0,
        request_hash="b" * 64, response_receipt={},
        response_hash="c" * 64, provider_stop_reason="tool_calls",
        usage={}, actual_credits=0, batch_hash="a" * 32,
        actions=[{"action_id": "44444444-4444-4444-4444-444444444444"}],
    )
    assert receipt.outcome is ActionMutationOutcome.COMPLETED
    assert receipt.run_status == "waiting_actions"
    assert database.calls[0][1]["p_actions"][0]["action_id"].startswith("4444")


def test_runtime_scope_cannot_construct_repository() -> None:
    database = Database({})
    database.scope = DatabaseScope(
        actor_user_id="11111111-1111-1111-1111-111111111111",
        org_id=None, access_kind=DatabaseAccessKind.RUNTIME,
        request_id="runtime",
    )
    with pytest.raises(ValueError, match="WORKER_SCOPED"):
        PostgresActionRepository(database)
