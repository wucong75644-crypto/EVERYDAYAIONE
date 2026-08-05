from __future__ import annotations

import asyncio

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.infrastructure.postgres.model_gateway import (
    PostgresModelGatewayRepository,
)


class _Response:
    def __init__(self, data: object) -> None:
        self.data = data


class _Call:
    def __init__(self, database: "_Database", name: str, params: dict[str, object]) -> None:
        self.database, self.name, self.params = database, name, params

    async def execute(self) -> _Response:
        self.database.calls.append((self.name, self.params))
        return _Response(self.database.response)


class _Database:
    def __init__(self, kind: DatabaseAccessKind, response: object | None = None) -> None:
        self.scope = DatabaseScope(None, None, kind, "gateway-test")
        self.response = response or {"outcome": "ok"}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]) -> _Call:
        return _Call(self, name, params)


BINDING = {
    "request_id": "11111111-1111-1111-1111-111111111111",
    "org_id": None,
    "user_id": "22222222-2222-2222-2222-222222222222",
    "session_id": "33333333-3333-3333-3333-333333333333",
    "run_id": "44444444-4444-4444-4444-444444444444",
    "model_step_id": "55555555-5555-5555-5555-555555555555",
    "model_attempt_id": "66666666-6666-6666-6666-666666666666",
    "execution_token": "77777777-7777-7777-7777-777777777777",
    "request_hash": "a" * 64,
    "runtime_worker_id": "runtime-worker",
    "attempt_state_version": 0,
    "model_id": "qwen-plus",
    "provider": "dashscope",
    "provider_revision": "model-revision",
    "model_revision": "model-revision",
    "purpose": "model.invoke",
    "tenant_kill_epoch": 0,
    "provider_kill_epoch": 0,
    "capability_kill_epoch": 0,
}


def test_runtime_repository_can_only_submit_and_secret_free_read() -> None:
    database = _Database(DatabaseAccessKind.AGENT_RUNTIME)
    repository = PostgresModelGatewayRepository(database)
    asyncio.run(repository.submit(**BINDING))
    read = {key: BINDING[key] for key in (
        "request_id", "org_id", "user_id", "run_id", "model_attempt_id",
        "execution_token", "request_hash",
    )}
    asyncio.run(repository.read(**read))
    assert [call[0] for call in database.calls] == [
        "submit_agent_runtime_model_gateway_operation",
        "read_agent_runtime_model_gateway_operation",
    ]
    with pytest.raises(PermissionError):
        asyncio.run(repository.claim(gateway_worker_id="gateway", **{
            key: value for key, value in BINDING.items() if key not in {"session_id", "model_step_id"}
        }))


def test_gateway_repository_routes_claim_and_mutations() -> None:
    database = _Database(DatabaseAccessKind.AGENT_MODEL_GATEWAY)
    repository = PostgresModelGatewayRepository(database)
    claim = {key: value for key, value in BINDING.items() if key not in {"session_id", "model_step_id"}}
    asyncio.run(repository.claim(gateway_worker_id="gateway", **claim))
    fence = {
        "operation_id": BINDING["request_id"],
        "claim_token": BINDING["execution_token"],
        "expected_state_version": 1,
        "execution_token": BINDING["execution_token"],
        "request_hash": BINDING["request_hash"],
        "provider_revision": BINDING["provider_revision"],
        "tenant_kill_epoch": 0,
        "provider_kill_epoch": 0,
        "capability_kill_epoch": 0,
    }
    asyncio.run(repository.mark_dispatched(**fence))
    asyncio.run(repository.renew(**fence))
    asyncio.run(repository.finalize(
        **fence, terminal_status="unknown", provider_request_id=None,
        response_started=False, response_hash=None, usage_summary={},
        terminal_error_code=None, ambiguity_code="DISCONNECT",
    ))
    asyncio.run(repository.recover(gateway_worker_id="gateway"))
    assert [call[0] for call in database.calls] == [
        "claim_agent_runtime_model_gateway_operation",
        "mark_agent_runtime_model_gateway_dispatched",
        "renew_agent_runtime_model_gateway_operation",
        "finalize_agent_runtime_model_gateway_operation",
        "recover_agent_runtime_model_gateway_operations",
    ]
    with pytest.raises(PermissionError):
        asyncio.run(repository.submit(**BINDING))


def test_repository_rejects_untrusted_scope_and_incomplete_binding() -> None:
    with pytest.raises(ValueError):
        PostgresModelGatewayRepository(_Database(DatabaseAccessKind.WORKER))
    repository = PostgresModelGatewayRepository(_Database(DatabaseAccessKind.AGENT_RUNTIME))
    with pytest.raises(ValueError, match="model_revision"):
        asyncio.run(repository.submit(**{
            key: value for key, value in BINDING.items() if key != "model_revision"
        }))
