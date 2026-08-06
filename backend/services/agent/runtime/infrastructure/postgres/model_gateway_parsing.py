"""Fail-closed parsing for atomic Model Gateway dispatch receipts."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    IdempotencyConflictError,
    PersistenceContractError,
)
from services.agent.runtime.ports.model_gateway import (
    ModelGatewayDispatchBinding,
    ModelGatewayDispatchOutcome,
    ModelGatewayDispatchReceipt,
)


def parse_gateway_dispatch_receipt(value: object) -> ModelGatewayDispatchReceipt:
    row = _mapping(value, "Model Gateway dispatch")
    outcome = _text(row, "outcome")
    if outcome == "fenced":
        raise FencingTokenMismatchError(outcome)
    if outcome == "idempotency_conflict":
        raise IdempotencyConflictError(outcome)
    try:
        parsed = ModelGatewayDispatchOutcome(outcome)
    except ValueError as error:
        raise PersistenceContractError(
            f"unknown Model Gateway dispatch outcome: {outcome}"
        ) from error
    if parsed is ModelGatewayDispatchOutcome.NOT_FOUND:
        return ModelGatewayDispatchReceipt(outcome=parsed)
    operation = _mapping(row.get("operation"), "Model Gateway operation")
    attempt_id = _uuid(row, "attempt_id")
    state_version = _integer(row, "state_version")
    worker_id = _text(row, "worker_id")
    if operation.get("model_attempt_id") != attempt_id:
        raise PersistenceContractError("Model Gateway attempt binding mismatch")
    if operation.get("attempt_state_version") != state_version:
        raise PersistenceContractError("Model Gateway version binding mismatch")
    return ModelGatewayDispatchReceipt(
        outcome=parsed,
        binding=ModelGatewayDispatchBinding(
            operation_id=_uuid(operation, "operation_id"),
            request_id=_uuid(operation, "request_id"),
            org_id=_uuid(operation, "org_id", optional=True),
            user_id=_uuid(operation, "user_id"),
            session_id=_uuid(operation, "session_id"),
            run_id=_uuid(operation, "run_id"),
            model_step_id=_uuid(operation, "model_step_id"),
            model_attempt_id=attempt_id,
            worker_id=worker_id,
            execution_token=_uuid(operation, "execution_token"),
            request_hash=_hash(operation, "request_hash"),
            attempt_state_version=state_version,
            model_id=_text(operation, "model_id"),
            provider=_text(operation, "provider"),
            provider_revision=_text(operation, "provider_revision"),
            model_revision=_text(operation, "model_revision"),
            purpose=_text(operation, "purpose"),
            tenant_kill_epoch=_integer(operation, "tenant_kill_epoch"),
            provider_kill_epoch=_integer(operation, "provider_kill_epoch"),
            capability_kill_epoch=_integer(operation, "capability_kill_epoch"),
        ),
    )


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PersistenceContractError(f"{context}: object response required")
    return value


def _text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PersistenceContractError(f"{field}: nonblank text required")
    return value


def _uuid(
    row: Mapping[str, Any], field: str, *, optional: bool = False,
) -> str | None:
    value = row.get(field)
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise PersistenceContractError(f"{field}: UUID required")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise PersistenceContractError(f"{field}: UUID required") from error


def _integer(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PersistenceContractError(f"{field}: nonnegative integer required")
    return value


def _hash(row: Mapping[str, Any], field: str) -> str:
    value = _text(row, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise PersistenceContractError(f"{field}: SHA-256 required")
    return value


__all__ = ["parse_gateway_dispatch_receipt"]
