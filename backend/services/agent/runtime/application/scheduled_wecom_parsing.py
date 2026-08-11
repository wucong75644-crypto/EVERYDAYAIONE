"""Strict application-level parsing for Scheduled WeCom recovery receipts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, TypeVar
from uuid import UUID

from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptStatus,
    DeliveryStatus,
    DispatchOutcome,
    DispatchPhase,
    ItemStatus,
    StartedRecoveryOutcome,
    StartedRecoveryResult,
)


EnumT = TypeVar("EnumT", bound=StrEnum)
_STARTED_RECOVERY_KEYS = {
    "outcome", "request_id", "recovery_worker_id", "org_id", "intent_id",
    "item_id", "attempt_id", "outcome_request_id", "dispatch_outcome",
    "attempt_status", "dispatch_phase", "item_status", "delivery_status",
    "delivery_state_version", "item_state_version", "recovered_at",
}


def _fail(code: str) -> PersistenceContractError:
    return PersistenceContractError(
        f"SCHEDULED_WECOM_STARTED_RECOVERY_CONTRACT_INVALID:{code}",
    )


def _text(row: Mapping[str, Any], field: str, *, maximum: int) -> str:
    value = row.get(field)
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > maximum
    ):
        raise _fail(field)
    return value


def _uuid(row: Mapping[str, Any], field: str) -> str:
    try:
        return str(UUID(_text(row, field, maximum=36)))
    except ValueError as exc:
        raise _fail(field) from exc


def _enum(row: Mapping[str, Any], field: str, kind: type[EnumT]) -> EnumT:
    try:
        return kind(_text(row, field, maximum=80))
    except ValueError as exc:
        raise _fail(field) from exc


def _version(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(field)
    return value


def _timestamp(row: Mapping[str, Any], field: str) -> datetime:
    value = row.get(field)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _fail(field) from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise _fail(field)
    return value


def validate_started_recovery_request(
    request_id: str, worker_id: str,
) -> tuple[str, str]:
    """Validate the exact stable identity before the mutating 227_48 RPC."""
    if not isinstance(request_id, str) or request_id != request_id.strip():
        raise _fail("request_id")
    try:
        normalized_request = str(UUID(request_id))
    except ValueError as exc:
        raise _fail("request_id") from exc
    if request_id != normalized_request:
        raise _fail("request_id")
    if (
        not isinstance(worker_id, str) or worker_id != worker_id.strip()
        or not 1 <= len(worker_id) <= 128
    ):
        raise _fail("recovery_worker_id")
    return normalized_request, worker_id


def parse_started_recovery(
    raw: object, *, request_id: str, worker_id: str,
) -> StartedRecoveryResult | None:
    """Parse the exact 227_48 JSON receipt without inferring hidden table fields."""
    if raw == {"outcome": "empty"}:
        return None
    if isinstance(raw, Mapping) and raw.get("outcome") in {
        "fenced", "not_found", "unavailable",
    }:
        outcome = raw.get("outcome")
        if set(raw) == {"outcome"}:
            raise _fail(f"started_recovery_{outcome}")
    if not isinstance(raw, Mapping) or set(raw) != _STARTED_RECOVERY_KEYS:
        raise _fail("started_recovery")

    returned_request = _uuid(raw, "request_id")
    returned_worker = _text(raw, "recovery_worker_id", maximum=128)
    expected_request, expected_worker = validate_started_recovery_request(
        request_id, worker_id,
    )
    if returned_request != expected_request or returned_worker != expected_worker:
        raise _fail("started_recovery_identity_changed")

    result = StartedRecoveryResult(
        outcome=_enum(raw, "outcome", StartedRecoveryOutcome),
        request_id=returned_request,
        recovery_worker_id=returned_worker,
        org_id=_uuid(raw, "org_id"),
        intent_id=_uuid(raw, "intent_id"),
        item_id=_uuid(raw, "item_id"),
        attempt_id=_uuid(raw, "attempt_id"),
        outcome_request_id=_uuid(raw, "outcome_request_id"),
        dispatch_outcome=_enum(raw, "dispatch_outcome", DispatchOutcome),
        attempt_status=_enum(raw, "attempt_status", AttemptStatus),
        dispatch_phase=_enum(raw, "dispatch_phase", DispatchPhase),
        item_status=_enum(raw, "item_status", ItemStatus),
        delivery_status=_enum(raw, "delivery_status", DeliveryStatus),
        delivery_state_version=_version(raw, "delivery_state_version"),
        item_state_version=_version(raw, "item_state_version"),
        recovered_at=_timestamp(raw, "recovered_at"),
    )
    if (
        result.outcome_request_id == result.request_id
        or result.dispatch_outcome is not DispatchOutcome.UNKNOWN
        or result.attempt_status is not AttemptStatus.UNKNOWN
        or result.dispatch_phase is not DispatchPhase.AMBIGUOUS
        or result.item_status is not ItemStatus.UNKNOWN
        or result.delivery_status is not DeliveryStatus.UNKNOWN
    ):
        raise _fail("started_recovery_result_fence")
    return result
