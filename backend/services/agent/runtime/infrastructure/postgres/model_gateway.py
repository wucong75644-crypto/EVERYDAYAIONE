"""Scoped PostgreSQL adapter for durable Model Gateway operation RPCs."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.infrastructure.postgres.model_gateway_parsing import (
    parse_gateway_dispatch_receipt,
)
from services.agent.runtime.ports.model_gateway import ModelGatewayDispatchReceipt


class PostgresModelGatewayRepository:
    """Expose only the RPC surface permitted by the scoped process identity."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        allowed = {
            DatabaseAccessKind.AGENT_RUNTIME,
            DatabaseAccessKind.AGENT_MODEL_GATEWAY,
        }
        if scope is None or scope.access_kind not in allowed:
            raise ValueError("MODEL_GATEWAY_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database
        self._access_kind = scope.access_kind

    async def _rpc(self, name: str, params: dict[str, object]) -> Mapping[str, object]:
        response = await self._database.rpc(name, params).execute()
        if not isinstance(response.data, Mapping):
            raise RuntimeError("MODEL_GATEWAY_RPC_RESPONSE_INVALID")
        return response.data

    def _require(self, expected: DatabaseAccessKind) -> None:
        if self._access_kind is not expected:
            raise PermissionError("MODEL_GATEWAY_REPOSITORY_SCOPE_MISMATCH")

    async def start_dispatch(
        self, **binding: object,
    ) -> ModelGatewayDispatchReceipt:
        self._require(DatabaseAccessKind.AGENT_RUNTIME)
        return parse_gateway_dispatch_receipt(await self._rpc(
            "start_agent_runtime_model_gateway_dispatch",
            _start_dispatch_params(binding),
        ))

    async def read(self, **binding: object) -> Mapping[str, object]:
        return await self._rpc(
            "read_agent_runtime_model_gateway_operation", _read_params(binding),
        )

    async def claim(
        self, *, gateway_worker_id: str, lease_seconds: int = 120,
        **binding: object,
    ) -> Mapping[str, object]:
        self._require(DatabaseAccessKind.AGENT_MODEL_GATEWAY)
        params = _claim_params(binding)
        params.update({
            "p_gateway_worker_id": gateway_worker_id,
            "p_lease_seconds": lease_seconds,
        })
        return await self._rpc(
            "claim_agent_runtime_model_gateway_operation_v2", params,
        )

    async def mark_dispatched(self, **fence: object) -> Mapping[str, object]:
        return await self._gateway_mutation(
            "mark_agent_runtime_model_gateway_dispatched", fence,
        )

    async def fail_before_dispatch(
        self, *, error_code: str, **fence: object,
    ) -> Mapping[str, object]:
        params = _predispatch_failure_params(fence)
        params["p_error_code"] = error_code
        return await self._gateway_rpc(
            "fail_agent_runtime_model_gateway_claim", params,
        )

    async def renew(
        self, *, lease_seconds: int = 120, **fence: object,
    ) -> Mapping[str, object]:
        params = _mutation_params(fence)
        params["p_lease_seconds"] = lease_seconds
        return await self._gateway_rpc(
            "renew_agent_runtime_model_gateway_operation", params,
        )

    async def finalize(
        self, *, terminal_status: str, provider_request_id: str | None,
        response_started: bool, response_hash: str | None,
        usage_summary: Mapping[str, object], terminal_error_code: str | None,
        ambiguity_code: str | None, **fence: object,
    ) -> Mapping[str, object]:
        params = _mutation_params(fence)
        params.update({
            "p_terminal_status": terminal_status,
            "p_provider_request_id": provider_request_id,
            "p_response_started": response_started,
            "p_response_hash": response_hash,
            "p_usage_summary": dict(usage_summary),
            "p_terminal_error_code": terminal_error_code,
            "p_ambiguity_code": ambiguity_code,
        })
        return await self._gateway_rpc(
            "finalize_agent_runtime_model_gateway_operation", params,
        )

    async def recover(
        self, *, gateway_worker_id: str, lease_seconds: int = 120,
        limit: int = 50,
    ) -> Mapping[str, object]:
        return await self._gateway_rpc(
            "recover_agent_runtime_model_gateway_operations", {
                "p_gateway_worker_id": gateway_worker_id,
                "p_lease_seconds": lease_seconds,
                "p_limit": limit,
            },
        )

    async def _gateway_mutation(
        self, name: str, fence: Mapping[str, object],
    ) -> Mapping[str, object]:
        return await self._gateway_rpc(name, _mutation_params(fence))

    async def _gateway_rpc(
        self, name: str, params: dict[str, object],
    ) -> Mapping[str, object]:
        self._require(DatabaseAccessKind.AGENT_MODEL_GATEWAY)
        return await self._rpc(name, params)


def _start_dispatch_params(values: Mapping[str, object]) -> dict[str, object]:
    names = (
        "request_id", "session_id", "run_id", "model_step_id",
        "model_attempt_id", "run_execution_token", "request_hash",
        "expected_attempt_version", "model_id", "provider",
        "provider_revision", "model_revision", "purpose",
    )
    return _prefixed(values, names)


def _read_params(values: Mapping[str, object]) -> dict[str, object]:
    return _prefixed(values, (
        "request_id", "org_id", "user_id", "run_id", "model_attempt_id",
        "execution_token", "request_hash",
    ))


def _claim_params(values: Mapping[str, object]) -> dict[str, object]:
    names = (
        "request_id", "runtime_worker_id", "org_id", "user_id", "run_id", "model_attempt_id",
        "execution_token", "request_hash", "attempt_state_version", "model_id",
        "provider", "provider_revision", "model_revision", "purpose",
        "tenant_kill_epoch", "provider_kill_epoch", "capability_kill_epoch",
    )
    return _prefixed(values, names)


def _mutation_params(values: Mapping[str, object]) -> dict[str, object]:
    params = _prefixed(values, (
        "operation_id", "claim_token",
        "execution_token", "request_hash", "provider_revision",
        "tenant_kill_epoch", "provider_kill_epoch", "capability_kill_epoch",
    ))
    if "expected_state_version" not in values:
        raise ValueError("MODEL_GATEWAY_BINDING_REQUIRED:expected_state_version")
    params["p_expected_operation_version"] = values["expected_state_version"]
    return params


def _predispatch_failure_params(
    values: Mapping[str, object],
) -> dict[str, object]:
    params = _prefixed(values, (
        "operation_id", "claim_token", "org_id", "execution_token",
        "request_hash", "provider_revision", "tenant_kill_epoch",
        "provider_kill_epoch", "capability_kill_epoch",
    ))
    if "expected_state_version" not in values:
        raise ValueError("MODEL_GATEWAY_BINDING_REQUIRED:expected_state_version")
    params["p_expected_operation_version"] = values["expected_state_version"]
    return params


def _prefixed(
    values: Mapping[str, object], names: tuple[str, ...],
) -> dict[str, object]:
    missing = [name for name in names if name not in values]
    if missing:
        raise ValueError(f"MODEL_GATEWAY_BINDING_REQUIRED:{','.join(missing)}")
    uuid_names = {
        "claim_token", "execution_token", "model_attempt_id", "model_step_id",
        "operation_id", "org_id", "request_id", "run_id", "session_id", "user_id",
        "run_execution_token",
    }
    return {
        f"p_{name}": (
            UUID(str(values[name]))
            if name in uuid_names and values[name] is not None else values[name]
        )
        for name in names
    }


__all__ = ["PostgresModelGatewayRepository"]
