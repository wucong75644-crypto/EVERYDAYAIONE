"""Fail-closed parsing for migration 217 ModelAttempt RPCs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    IdempotencyConflictError,
    LeaseExpiredError,
    PersistenceContractError,
    StaleVersionError,
    TerminalConflictError,
)
from services.agent.runtime.domain.model_attempt import (
    ModelAttemptStatus,
    ModelDispatchPhase,
    ModelLateOutcome,
    ModelRetryDisposition,
)
from services.agent.runtime.ports.model_attempt import (
    ModelAttemptOutcome,
    ModelAttemptReceipt,
    ModelAttemptSnapshot,
)


_ERRORS = {
    "ownership_lost": FencingTokenMismatchError,
    "lease_expired": LeaseExpiredError,
    "stale_version": StaleVersionError,
    "idempotency_conflict": IdempotencyConflictError,
    "request_hash_conflict": IdempotencyConflictError,
    "terminal_conflict": TerminalConflictError,
}


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PersistenceContractError(f"{context}: object response required")
    return value


def _text(
    row: Mapping[str, Any], field: str, *, optional: bool = False,
) -> str | None:
    value = row.get(field)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PersistenceContractError(f"{field}: nonblank text required")
    return value


def _uuid(
    row: Mapping[str, Any], field: str, *, optional: bool = False,
) -> str | None:
    value = _text(row, field, optional=optional)
    if value is None:
        return None
    try:
        return str(UUID(value))
    except ValueError as error:
        raise PersistenceContractError(f"{field}: UUID required") from error


def _integer(
    row: Mapping[str, Any], field: str, *, optional: bool = False,
    minimum: int = 0,
) -> int | None:
    value = row.get(field)
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PersistenceContractError(f"{field}: invalid integer")
    return value


def _time(row: Mapping[str, Any], field: str) -> datetime:
    value = row.get(field)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise PersistenceContractError(f"{field}: timestamp required") from error
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PersistenceContractError(f"{field}: aware timestamp required")
    return value


def parse_attempt_receipt(
    value: object,
    allowed: set[ModelAttemptOutcome],
) -> ModelAttemptReceipt:
    row = _mapping(value, "ModelAttempt mutation")
    name = _text(row, "outcome")
    error = _ERRORS.get(name)
    if error is not None:
        raise error(name)
    try:
        parsed = ModelAttemptOutcome(name)
    except ValueError as error_value:
        raise PersistenceContractError(
            f"unknown ModelAttempt outcome: {name}"
        ) from error_value
    if parsed not in allowed:
        raise PersistenceContractError(
            f"unexpected ModelAttempt outcome: {name}"
        )
    return ModelAttemptReceipt(
        outcome=parsed,
        attempt_id=_uuid(row, "attempt_id", optional=True),
        model_step_id=_uuid(row, "model_step_id", optional=True),
        status=_enum(row, "status", ModelAttemptStatus),
        dispatch_phase=_enum(row, "dispatch_phase", ModelDispatchPhase),
        retry_disposition=_enum(
            row, "retry_disposition", ModelRetryDisposition,
        ),
        state_version=_integer(row, "state_version", optional=True),
        attempt_number=_integer(
            row, "attempt_number", optional=True, minimum=1,
        ),
        execution_token=_uuid(row, "execution_token", optional=True),
        lease_expires_at=(
            _time(row, "lease_expires_at")
            if row.get("lease_expires_at") is not None else None
        ),
        event_sequence=_integer(
            row, "event_sequence", optional=True, minimum=1,
        ),
        settlement_outcome=_text(
            row, "settlement_outcome", optional=True,
        ),
    )


def parse_attempt_snapshot(value: object) -> ModelAttemptSnapshot | None:
    result = _mapping(value, "ModelAttempt readback")
    outcome = _text(result, "outcome")
    if outcome == "not_found":
        return None
    if outcome != "found":
        raise PersistenceContractError(
            f"unknown ModelAttempt read outcome: {outcome}"
        )
    row = _mapping(result.get("attempt"), "ModelAttempt")
    late = _enum(row, "late_outcome", ModelLateOutcome)
    return ModelAttemptSnapshot(
        attempt_id=_uuid(row, "id"),
        model_step_id=_uuid(row, "model_step_id"),
        run_id=_uuid(row, "run_id"),
        attempt_number=_integer(row, "attempt_number", minimum=1),
        request_hash=_text(row, "request_hash"),
        idempotency_key=_text(row, "idempotency_key"),
        provider=_text(row, "provider"),
        provider_request_id=_text(
            row, "provider_request_id", optional=True,
        ),
        status=_enum(row, "status", ModelAttemptStatus, required=True),
        dispatch_phase=_enum(
            row, "dispatch_phase", ModelDispatchPhase, required=True,
        ),
        retry_disposition=_enum(
            row, "retry_disposition", ModelRetryDisposition, required=True,
        ),
        response_hash=_text(row, "response_hash", optional=True),
        late_outcome=late,
        late_actual_credits=_integer(
            row, "late_actual_credits", optional=True,
        ),
        late_ambiguity_evidence=_json_object(
            row, "late_ambiguity_evidence",
        ),
        terminal_error_code=_text(
            row, "terminal_error_code", optional=True,
        ),
        state_version=_integer(row, "state_version"),
    )


def _enum(
    row: Mapping[str, Any], field: str, enum_type: type,
    *, required: bool = False,
) -> Any:
    value = _text(row, field, optional=not required)
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as error:
        raise PersistenceContractError(
            f"{field}: unknown {enum_type.__name__}"
        ) from error


def _json_object(
    row: Mapping[str, Any], field: str,
) -> Mapping[str, object] | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PersistenceContractError(f"{field}: object required")
    return value
