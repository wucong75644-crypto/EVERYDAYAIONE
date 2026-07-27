"""Fail-closed parsing for Agent Runtime PostgreSQL RPC responses."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, TypeVar
from uuid import UUID

from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    IdempotencyConflictError,
    InvalidTransitionError,
    LeaseExpiredError,
    PersistenceContractError,
    StaleVersionError,
    TerminalConflictError,
)
from services.agent.runtime.ports.repository import (
    MutationOutcome,
    MutationReceipt,
)


EnumT = TypeVar("EnumT", bound=StrEnum)

_ERROR_OUTCOMES = {
    "ownership_lost": FencingTokenMismatchError,
    "lease_expired": LeaseExpiredError,
    "stale_version": StaleVersionError,
    "invalid_transition": InvalidTransitionError,
    "idempotency_conflict": IdempotencyConflictError,
    "terminal_conflict": TerminalConflictError,
}


def require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PersistenceContractError(f"{context}: object response required")
    return value


def require_list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise PersistenceContractError(f"{context}: array response required")
    return value


def require_text(
    value: Mapping[str, Any], field: str, *, optional: bool = False,
) -> str | None:
    item = value.get(field)
    if item is None and optional:
        return None
    if not isinstance(item, str) or not item.strip():
        raise PersistenceContractError(f"{field}: nonblank text required")
    return item


def require_uuid(
    value: Mapping[str, Any], field: str, *, optional: bool = False,
) -> str | None:
    item = require_text(value, field, optional=optional)
    if item is None:
        return None
    try:
        return str(UUID(item))
    except ValueError as exc:
        raise PersistenceContractError(f"{field}: UUID required") from exc


def require_int(
    value: Mapping[str, Any], field: str, *, minimum: int = 0,
    optional: bool = False,
) -> int | None:
    item = value.get(field)
    if item is None and optional:
        return None
    if isinstance(item, bool) or not isinstance(item, int) or item < minimum:
        raise PersistenceContractError(f"{field}: integer >= {minimum} required")
    return item


def require_datetime(value: Mapping[str, Any], field: str) -> datetime:
    item = value.get(field)
    if isinstance(item, str):
        try:
            item = datetime.fromisoformat(item.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PersistenceContractError(
                f"{field}: timestamp required",
            ) from exc
    if not isinstance(item, datetime) or item.utcoffset() is None:
        raise PersistenceContractError(f"{field}: aware timestamp required")
    return item


def require_enum(
    value: Mapping[str, Any], field: str, enum_type: type[EnumT],
) -> EnumT:
    item = require_text(value, field)
    try:
        return enum_type(item)
    except ValueError as exc:
        raise PersistenceContractError(
            f"{field}: unknown {enum_type.__name__}",
        ) from exc


def require_json_object(
    value: Mapping[str, Any], field: str,
) -> Mapping[str, object]:
    item = value.get(field)
    if not isinstance(item, Mapping):
        raise PersistenceContractError(f"{field}: JSON object required")
    return item


def outcome(value: Mapping[str, Any], allowed: set[str]) -> str:
    name = require_text(value, "outcome")
    error = _ERROR_OUTCOMES.get(name)
    if error is not None:
        raise error(name)
    if name not in allowed:
        raise PersistenceContractError(f"unknown RPC outcome: {name}")
    return name


def mutation_receipt(
    value: object, allowed: set[MutationOutcome],
) -> MutationReceipt:
    row = require_mapping(value, "mutation")
    name = outcome(row, {item.value for item in allowed})
    return MutationReceipt(
        outcome=MutationOutcome(name),
        entity_id=require_uuid(row, "entity_id", optional=True),
        state_version=require_int(
            row, "state_version", optional=True,
        ),
        event_sequence=require_int(
            row, "event_sequence", minimum=1, optional=True,
        ),
        result_entity_id=require_uuid(
            row, "result_entity_id", optional=True,
        ),
    )
