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
            access_kind=DatabaseAccessKind.AGENT_RUNTIME,
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
        "complete_model_attempt_with_raw_actions": {
            "outcome": "completed",
            "attempt_id": "11111111-1111-1111-1111-111111111111",
            "model_step_id": "22222222-2222-2222-2222-222222222222",
            "run_id": "33333333-3333-3333-3333-333333333333",
            "run_status": "waiting_actions",
            "blocking_action_count": 1,
            "batch_hash": "a" * 64,
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
        usage={}, actual_credits=0, batch_hash="a" * 64,
        actions=[{"action_id": "44444444-4444-4444-4444-444444444444"}],
    )
    assert receipt.outcome is ActionMutationOutcome.COMPLETED
    assert receipt.run_status == "waiting_actions"
    assert database.calls[0][1]["p_actions"][0]["action_id"].startswith("4444")


@pytest.mark.asyncio
async def test_claim_and_readback_forward_stable_request_identity() -> None:
    attempt = {
        "id": "11111111-1111-1111-1111-111111111111",
        "action_id": "22222222-2222-2222-2222-222222222222",
        "worker_id": "worker-1",
        "claim_request_id": "claim-1",
        "execution_token": "33333333-3333-3333-3333-333333333333",
        "lease_expires_at": "2026-07-27T12:00:00+00:00",
        "status": "claimed",
    }
    database = Database({
        "claim_ready_agent_actions_v2": {
            "outcome": "claimed", "attempts": [attempt],
        },
        "get_agent_action_claim_batch": {
            "outcome": "found", "attempts": [attempt],
        },
    })
    repository = PostgresActionRepository(database)
    claimed = await repository.claim_ready(
        worker_id="worker-1", claim_request_id="claim-1",
    )
    recovered = await repository.get_claim_batch(
        worker_id="worker-1", claim_request_id="claim-1",
    )
    assert claimed.attempts == recovered.attempts
    assert database.calls == [
        ("claim_ready_agent_actions_v2", {
            "p_worker_id": "worker-1", "p_claim_request_id": "claim-1",
            "p_batch_size": 10, "p_lease_seconds": 120,
        }),
        ("get_agent_action_claim_batch", {
            "p_worker_id": "worker-1", "p_claim_request_id": "claim-1",
        }),
    ]


@pytest.mark.asyncio
async def test_get_action_exposes_narrow_read_rpc() -> None:
    action = {
        "id": "22222222-2222-2222-2222-222222222222",
        "status": "queued", "request_hash": "a" * 64,
    }
    database = Database({
        "get_agent_action": {
            "outcome": "found", "action": action,
            "attempt": None, "result": None,
        },
    })
    receipt = await PostgresActionRepository(database).get_action(
        action_id=action["id"],
    )
    assert receipt.action == action
    assert database.calls[0][0] == "get_agent_action"


def test_claim_attempt_missing_identity_fails_closed() -> None:
    with pytest.raises(PersistenceContractError, match="claim_request_id"):
        parse_action_receipt({
            "outcome": "claimed",
            "attempts": [{
                "id": "11111111-1111-1111-1111-111111111111",
                "action_id": "22222222-2222-2222-2222-222222222222",
                "worker_id": "worker-1",
                "execution_token": "33333333-3333-3333-3333-333333333333",
                "lease_expires_at": "2026-07-27T12:00:00+00:00",
                "status": "claimed",
            }],
        })


def test_runtime_scope_cannot_construct_repository() -> None:
    database = Database({})
    database.scope = DatabaseScope(
        actor_user_id="11111111-1111-1111-1111-111111111111",
        org_id=None, access_kind=DatabaseAccessKind.RUNTIME,
        request_id="runtime",
    )
    with pytest.raises(ValueError, match="WORKER_SCOPED"):
        PostgresActionRepository(database)
