"""Fail-closed parsing for migration 218 Action RPCs."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping
from uuid import UUID

from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    IdempotencyConflictError,
    LeaseExpiredError,
    PersistenceContractError,
    StaleVersionError,
    TerminalConflictError,
)
from services.agent.runtime.ports.action_repository import (
    ActionMutationOutcome,
    ActionMutationReceipt,
)


_ERRORS = {
    "ownership_lost": FencingTokenMismatchError,
    "lease_expired": LeaseExpiredError,
    "stale_version": StaleVersionError,
    "request_hash_conflict": IdempotencyConflictError,
    "batch_hash_conflict": IdempotencyConflictError,
    "receipt_conflict": IdempotencyConflictError,
    "terminal_conflict": TerminalConflictError,
}


def parse_action_receipt(value: object) -> ActionMutationReceipt:
    if not isinstance(value, Mapping):
        raise PersistenceContractError("Action RPC object response required")
    name = value.get("outcome")
    if not isinstance(name, str):
        raise PersistenceContractError("Action RPC outcome required")
    error_type = _ERRORS.get(name)
    if error_type is not None:
        raise error_type(name)
    try:
        outcome = ActionMutationOutcome(name)
    except ValueError as error:
        raise PersistenceContractError(
            f"unknown Action outcome: {name}"
        ) from error
    return ActionMutationReceipt(
        outcome=outcome,
        action_id=_uuid(value.get("action_id")),
        attempt_id=_uuid(value.get("attempt_id")),
        model_step_id=_uuid(value.get("model_step_id")),
        run_id=_uuid(value.get("run_id")),
        run_status=_text(value.get("run_status")),
        state_version=_integer(value.get("state_version")),
        blocking_action_count=_integer(value.get("blocking_action_count")),
        batch_hash=_hash(value.get("batch_hash")),
        result_hash=_hash(value.get("result_hash")),
        execution_token=_uuid(value.get("execution_token")),
        lease_expires_at=_time(value.get("lease_expires_at")),
        action_ids=_uuid_tuple(value.get("action_ids")),
        attempts=_mapping_tuple(value.get("attempts")),
    )


def _text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PersistenceContractError("nonblank text required")
    return value


def _uuid(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return str(UUID(text))
    except ValueError as error:
        raise PersistenceContractError("UUID required") from error


def _integer(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PersistenceContractError("nonnegative integer required")
    return value


def _hash(value: object) -> str | None:
    text = _text(value)
    if text is not None and (
        len(text) not in (32, 64)
        or any(character not in "0123456789abcdef" for character in text)
    ):
        raise PersistenceContractError("canonical hash required")
    return text


def _time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise PersistenceContractError("timestamp required") from error
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PersistenceContractError("aware timestamp required")
    return value


def _uuid_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PersistenceContractError("Action IDs array required")
    parsed = tuple(_uuid(item) for item in value)
    if any(item is None for item in parsed):
        raise PersistenceContractError("Action ID UUID required")
    return tuple(item for item in parsed if item is not None)


def _mapping_tuple(value: object) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise PersistenceContractError("Attempt objects array required")
    return tuple(value)
