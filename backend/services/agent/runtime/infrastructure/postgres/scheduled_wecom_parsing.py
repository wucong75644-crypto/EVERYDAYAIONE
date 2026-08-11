"""Failure-closed parsers for Scheduled Runtime WeCom RPC receipts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Mapping, TypeVar
from uuid import UUID

from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DeliveryClaim,
    DeliveryClaimKind,
    DeliveryClaimOutcome,
    DeliveryFence,
    DeliveryStatus,
    DispatchAttempt,
    DispatchOutcome,
    DispatchOutcomeReceipt,
    DispatchPayloadVersions,
    DispatchPhase,
    ItemStatus,
    PreparedRecovery,
    ProviderDispatchIdentity,
    ProviderReceiptEvidence,
    ReceiptMetadata,
    ReceiptType,
    ReconcileClaim,
    ReconcileClaimOutcome,
    ReconcileResult,
    ReconcileResultReceipt,
    RecordOutcome,
    RecoveryOutcome,
)


EnumT = TypeVar("EnumT", bound=StrEnum)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]+$")


def _fail(code: str) -> PersistenceContractError:
    return PersistenceContractError(f"SCHEDULED_WECOM_RPC_CONTRACT_INVALID:{code}")


def _row(raw: object, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != keys:
        raise _fail(code)
    return raw


def _outcome_only(raw: object, allowed: set[str], code: str) -> str | None:
    if not isinstance(raw, Mapping):
        raise _fail(code)
    value = raw.get("outcome")
    if value in allowed and set(raw) == {"outcome"}:
        return str(value)
    return None


def _text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _fail(field)
    return value


def _optional_text(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    return _text(row, field)


def _uuid(row: Mapping[str, Any], field: str) -> str:
    value = _text(row, field)
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise _fail(field) from exc


def _optional_uuid(row: Mapping[str, Any], field: str) -> str | None:
    if row.get(field) is None:
        return None
    return _uuid(row, field)


def _integer(row: Mapping[str, Any], field: str, minimum: int = 0) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(field)
    return value


def _bounded_integer(
    row: Mapping[str, Any], field: str, minimum: int, maximum: int,
) -> int:
    value = _integer(row, field, minimum)
    if value > maximum:
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


def _enum(row: Mapping[str, Any], field: str, kind: type[EnumT]) -> EnumT:
    try:
        return kind(_text(row, field))
    except ValueError as exc:
        raise _fail(field) from exc


def _identity(row: Mapping[str, Any]) -> ProviderDispatchIdentity:
    request_id = _text(row, "provider_request_id")
    key = _text(row, "idempotency_key")
    if not 8 <= len(request_id) <= 200 or not _HASH.fullmatch(key):
        raise _fail("provider_identity")
    return ProviderDispatchIdentity(
        provider_request_id=request_id,
        idempotency_key=key,
        provider_revision=_integer(row, "provider_revision", 1),
    )


def _same_identity(
    actual: ProviderDispatchIdentity, expected: ProviderDispatchIdentity,
) -> None:
    if actual != expected:
        raise _fail("provider_identity_changed")


def parse_delivery_claim(raw: object) -> DeliveryClaim | None:
    if _outcome_only(raw, {"empty"}, "delivery_claim"):
        return None
    keys = {
        "outcome", "request_id", "claim_request_id", "intent_id", "item_id",
        "worker_id", "claim_kind", "lease_token", "lease_seconds",
        "lease_expires_at", "previous_claim_request_id", "state_version",
        "delivery_state_version", "item_state_version",
    }
    row = _row(raw, keys, "delivery_claim")
    request_id = _uuid(row, "request_id")
    delivery_version = _integer(row, "delivery_state_version", 1)
    if request_id != _uuid(row, "claim_request_id"):
        raise _fail("claim_request_id_changed")
    if delivery_version != _integer(row, "state_version", 1):
        raise _fail("delivery_state_version_changed")
    return DeliveryClaim(
        outcome=_enum(row, "outcome", DeliveryClaimOutcome),
        kind=_enum(row, "claim_kind", DeliveryClaimKind),
        fence=DeliveryFence(
            intent_id=_uuid(row, "intent_id"), item_id=_uuid(row, "item_id"),
            claim_request_id=request_id, lease_token=_uuid(row, "lease_token"),
            worker_id=_text(row, "worker_id"),
            delivery_state_version=delivery_version,
            item_state_version=_integer(row, "item_state_version"),
        ),
        lease_seconds=_bounded_integer(row, "lease_seconds", 5, 900),
        lease_expires_at=_timestamp(row, "lease_expires_at"),
        previous_claim_request_id=_optional_uuid(row, "previous_claim_request_id"),
    )


_ATTEMPT_KEYS = {
    "outcome", "attempt_id", "item_id", "attempt_number", "provider_request_id",
    "idempotency_key", "provider_revision", "status", "delivery_state_version",
    "item_state_version",
}


class AttemptRpcOperation(StrEnum):
    PREPARE = "prepare"
    START = "start"
    READ = "read"


_READBACK_STATUSES = frozenset({
    AttemptStatus.PREPARED,
    AttemptStatus.DISPATCH_STARTED,
    AttemptStatus.ACCEPTED,
    AttemptStatus.REJECTED,
})
_ATTEMPT_OPERATION_MATRIX = {
    AttemptRpcOperation.PREPARE: {
        AttemptOperationOutcome.PREPARED: frozenset({AttemptStatus.PREPARED}),
        AttemptOperationOutcome.READBACK: _READBACK_STATUSES,
    },
    AttemptRpcOperation.START: {
        AttemptOperationOutcome.DISPATCH_STARTED: frozenset({AttemptStatus.DISPATCH_STARTED}),
        AttemptOperationOutcome.READBACK: _READBACK_STATUSES - {AttemptStatus.PREPARED},
    },
    AttemptRpcOperation.READ: {
        AttemptOperationOutcome.READBACK: _READBACK_STATUSES,
    },
}


def parse_attempt(raw: object, fence: DeliveryFence, identity: ProviderDispatchIdentity, *,
    operation: AttemptRpcOperation, payload_versions: DispatchPayloadVersions | None = None,
) -> DispatchAttempt:
    minimal = _outcome_only(raw, {"fenced", "not_found"}, "dispatch_attempt")
    if minimal is not None:
        raise _fail(f"dispatch_attempt_{minimal}")
    row = _row(raw, _ATTEMPT_KEYS, "dispatch_attempt")
    actual_identity = _identity(row)
    _same_identity(actual_identity, identity)
    if _uuid(row, "item_id") != fence.item_id:
        raise _fail("attempt_item_changed")
    outcome = _enum(row, "outcome", AttemptOperationOutcome)
    status = _enum(row, "status", AttemptStatus)
    if status not in _ATTEMPT_OPERATION_MATRIX[operation].get(outcome, ()):
        raise _fail("dispatch_attempt_outcome_status")
    return DispatchAttempt(
        outcome=outcome,
        fence=DeliveryFence(
            intent_id=fence.intent_id, item_id=fence.item_id,
            claim_request_id=fence.claim_request_id, lease_token=fence.lease_token,
            worker_id=fence.worker_id,
            delivery_state_version=_integer(row, "delivery_state_version", 1),
            item_state_version=_integer(row, "item_state_version", 1),
        ),
        attempt_id=_uuid(row, "attempt_id"),
        attempt_number=_integer(row, "attempt_number", 1),
        identity=actual_identity,
        payload_versions=payload_versions or DispatchPayloadVersions(
            delivery_state_version=fence.delivery_state_version, item_state_version=fence.item_state_version,
        ),
        status=status,
    )


def parse_prepared_recovery(
    raw: object, *, request_id: str, worker_id: str,
) -> PreparedRecovery | None:
    if _outcome_only(raw, {"empty"}, "prepared_recovery"):
        return None
    keys = _ATTEMPT_KEYS | {"intent_id", "claim_request_id", "worker_id", "lease_token",
                            "lease_expires_at", "prepared_delivery_state_version", "prepared_item_state_version"}
    row = _row(raw, keys, "prepared_recovery")
    outcome = _enum(row, "outcome", RecoveryOutcome)
    returned_request = _uuid(row, "claim_request_id")
    returned_worker = _text(row, "worker_id")
    if len(returned_worker) > 128:
        raise _fail("worker_id")
    if returned_request != str(UUID(request_id)) or returned_worker != worker_id:
        raise _fail("prepared_recovery_identity_changed")
    fence = DeliveryFence(
        intent_id=_uuid(row, "intent_id"), item_id=_uuid(row, "item_id"),
        claim_request_id=returned_request, lease_token=_uuid(row, "lease_token"),
        worker_id=returned_worker,
        delivery_state_version=_integer(row, "delivery_state_version", 1),
        item_state_version=_integer(row, "item_state_version", 1),
    )
    identity = _identity(row)
    attempt = DispatchAttempt(
        outcome=AttemptOperationOutcome.READBACK, fence=fence,
        attempt_id=_uuid(row, "attempt_id"),
        attempt_number=_integer(row, "attempt_number", 1), identity=identity,
        payload_versions=DispatchPayloadVersions(
            delivery_state_version=_integer(row, "prepared_delivery_state_version", 1),
            item_state_version=_integer(row, "prepared_item_state_version", 0)),
        status=_enum(row, "status", AttemptStatus),
    )
    return PreparedRecovery(
        outcome=outcome, attempt=attempt,
        lease_expires_at=_timestamp(row, "lease_expires_at"),
    )


def metadata_params(metadata: ReceiptMetadata) -> dict[str, object]:
    values = {
        "provider_message_id": metadata.provider_message_id,
        "trace_id": metadata.trace_id,
        "provider_code": metadata.provider_code,
        "http_status": metadata.http_status,
        "wecom_errcode": metadata.wecom_errcode,
    }
    raw = {key: value for key, value in values.items() if value is not None}
    _metadata({"metadata": raw}, "metadata")
    return raw


def validate_evidence(evidence: ProviderReceiptEvidence) -> None:
    _evidence({
        "receipt_type": evidence.receipt_type.value,
        "receipt_hash": evidence.receipt_hash,
        "receipt_code": evidence.receipt_code,
        "receipt_metadata": metadata_params(evidence.metadata),
    }, prefix="receipt")


def _metadata(row: Mapping[str, Any], field: str) -> ReceiptMetadata:
    raw = row.get(field)
    allowed = {
        "provider_message_id", "trace_id", "provider_code", "http_status", "wecom_errcode",
    }
    if not isinstance(raw, Mapping) or not set(raw).issubset(allowed):
        raise _fail(field)
    limits = {"provider_message_id": 200, "trace_id": 200, "provider_code": 80}
    for key, limit in limits.items():
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, str) or not value or not _IDENTIFIER.fullmatch(value)
            or len(value) > limit
        ):
            raise _fail(field)
    http_status = raw.get("http_status")
    wecom_errcode = raw.get("wecom_errcode")
    if http_status is not None and (
        isinstance(http_status, bool) or not isinstance(http_status, int)
        or not 100 <= http_status <= 599
    ):
        raise _fail(field)
    if wecom_errcode is not None and (
        isinstance(wecom_errcode, bool) or not isinstance(wecom_errcode, int)
        or not -(2**31) <= wecom_errcode < 2**31
    ):
        raise _fail(field)
    return ReceiptMetadata(
        provider_message_id=raw.get("provider_message_id"), trace_id=raw.get("trace_id"),
        provider_code=raw.get("provider_code"), http_status=http_status,
        wecom_errcode=wecom_errcode,
    )


def _evidence(
    row: Mapping[str, Any], *, prefix: str,
) -> ProviderReceiptEvidence:
    receipt_hash = _text(row, f"{prefix}_hash")
    code = _optional_text(row, f"{prefix}_code")
    if not _HASH.fullmatch(receipt_hash) or (code is not None and not _CODE.fullmatch(code)):
        raise _fail(f"{prefix}_evidence")
    return ProviderReceiptEvidence(
        receipt_type=_enum(row, f"{prefix}_type", ReceiptType),
        receipt_hash=receipt_hash, receipt_code=code,
        metadata=_metadata(row, f"{prefix}_metadata"),
    )


def parse_dispatch_outcome(raw: object) -> DispatchOutcomeReceipt:
    minimal = _outcome_only(raw, {"fenced"}, "dispatch_outcome")
    if minimal is not None:
        raise _fail("dispatch_outcome_fenced")
    keys = {
        "outcome", "request_id", "intent_id", "item_id", "attempt_id",
        "dispatch_outcome", "receipt_type", "receipt_hash", "receipt_code",
        "receipt_metadata", "attempt_status", "item_status", "delivery_status",
        "delivery_state_version", "item_state_version",
    }
    row = _row(raw, keys, "dispatch_outcome")
    dispatch_outcome = _enum(row, "dispatch_outcome", DispatchOutcome)
    evidence = None if dispatch_outcome is DispatchOutcome.UNKNOWN else _evidence(
        row, prefix="receipt",
    )
    if dispatch_outcome is DispatchOutcome.UNKNOWN and any(
        row.get(field) is not None for field in ("receipt_type", "receipt_hash", "receipt_code")
    ):
        raise _fail("unknown_receipt_evidence")
    if dispatch_outcome is DispatchOutcome.UNKNOWN and row.get("receipt_metadata") != {}:
        raise _fail("unknown_receipt_metadata")
    attempt_status = _enum(row, "attempt_status", AttemptStatus)
    if attempt_status.value != dispatch_outcome.value:
        raise _fail("dispatch_attempt_status")
    item_status = _enum(row, "item_status", ItemStatus)
    delivery_status = _enum(row, "delivery_status", DeliveryStatus)
    allowed_states = {
        DispatchOutcome.ACCEPTED: (
            ItemStatus.ACCEPTED,
            {DeliveryStatus.CLAIMED, DeliveryStatus.COMPLETED, DeliveryStatus.PARTIAL},
        ),
        DispatchOutcome.REJECTED: (
            ItemStatus.FAILED,
            {DeliveryStatus.CLAIMED, DeliveryStatus.PARTIAL, DeliveryStatus.FAILED},
        ),
        DispatchOutcome.UNKNOWN: (ItemStatus.UNKNOWN, {DeliveryStatus.UNKNOWN}),
    }
    expected_item, allowed_delivery = allowed_states[dispatch_outcome]
    if item_status is not expected_item or delivery_status not in allowed_delivery:
        raise _fail("dispatch_outcome_state")
    return DispatchOutcomeReceipt(
        outcome=_enum(row, "outcome", RecordOutcome), request_id=_uuid(row, "request_id"),
        intent_id=_uuid(row, "intent_id"), item_id=_uuid(row, "item_id"),
        attempt_id=_uuid(row, "attempt_id"), dispatch_outcome=dispatch_outcome,
        evidence=evidence, attempt_status=attempt_status,
        item_status=item_status, delivery_status=delivery_status,
        delivery_state_version=_integer(row, "delivery_state_version", 1),
        item_state_version=_integer(row, "item_state_version", 1),
    )


_RECONCILE_KEYS = {
    "outcome", "request_id", "intent_id", "org_id", "item_id", "attempt_id", "worker_id",
    "reconcile_token", "lease_seconds", "lease_expires_at", "claimed_lease_expires_at",
    "claim_delivery_state_version", "claim_item_state_version", "delivery_state_version",
    "item_state_version", "delivery_status", "item_status", "attempt_status",
    "dispatch_phase", "provider_request_id", "idempotency_key", "provider_revision",
}


def parse_reconcile_claim(raw: object) -> ReconcileClaim | None:
    if _outcome_only(raw, {"empty", "not_found"}, "reconcile_claim"):
        return None
    row = _row(raw, _RECONCILE_KEYS, "reconcile_claim")
    claim = ReconcileClaim(
        outcome=_enum(row, "outcome", ReconcileClaimOutcome),
        request_id=_uuid(row, "request_id"), intent_id=_uuid(row, "intent_id"),
        org_id=_uuid(row, "org_id"), item_id=_uuid(row, "item_id"), attempt_id=_uuid(row, "attempt_id"),
        worker_id=_text(row, "worker_id"), reconcile_token=_uuid(row, "reconcile_token"),
        lease_seconds=_bounded_integer(row, "lease_seconds", 5, 900),
        lease_expires_at=_timestamp(row, "lease_expires_at"),
        claimed_lease_expires_at=_timestamp(row, "claimed_lease_expires_at"),
        claim_delivery_state_version=_integer(row, "claim_delivery_state_version", 1),
        claim_item_state_version=_integer(row, "claim_item_state_version", 1),
        delivery_state_version=_integer(row, "delivery_state_version", 1),
        item_state_version=_integer(row, "item_state_version", 1),
        delivery_status=_enum(row, "delivery_status", DeliveryStatus),
        item_status=_enum(row, "item_status", ItemStatus),
        attempt_status=_enum(row, "attempt_status", AttemptStatus),
        dispatch_phase=_enum(row, "dispatch_phase", DispatchPhase),
        identity=_identity(row),
    )
    if (
        claim.delivery_status not in {DeliveryStatus.UNKNOWN, DeliveryStatus.RECONCILE_REQUIRED}
        or claim.item_status not in {ItemStatus.UNKNOWN, ItemStatus.RECONCILE_REQUIRED}
        or claim.attempt_status is not AttemptStatus.UNKNOWN
        or claim.dispatch_phase is not DispatchPhase.AMBIGUOUS
    ):
        raise _fail("reconcile_claim_state")
    return claim


def parse_reconcile_result(raw: object, *, definitive: bool) -> ReconcileResultReceipt:
    if _outcome_only(raw, {"fenced"}, "reconcile_result"):
        raise _fail("reconcile_result_fenced")
    shared = {
        "outcome", "request_id", "claim_request_id", "intent_id", "item_id", "attempt_id",
        "reconcile_result", "readback_type", "readback_hash", "readback_code",
        "readback_metadata", "attempt_status", "dispatch_phase", "item_status",
        "delivery_status", "delivery_state_version", "item_state_version",
    }
    variant = {"resolved_at"} if definitive else {"delay_seconds", "next_attempt_at"}
    row = _row(raw, shared | variant, "reconcile_result")
    result = _enum(row, "reconcile_result", ReconcileResult)
    if definitive == (result is ReconcileResult.STILL_UNKNOWN):
        raise _fail("reconcile_result_variant")
    receipt = ReconcileResultReceipt(
        outcome=_enum(row, "outcome", RecordOutcome), request_id=_uuid(row, "request_id"),
        claim_request_id=_uuid(row, "claim_request_id"), intent_id=_uuid(row, "intent_id"),
        item_id=_uuid(row, "item_id"), attempt_id=_uuid(row, "attempt_id"),
        reconcile_result=result, evidence=_evidence(row, prefix="readback"),
        attempt_status=_enum(row, "attempt_status", AttemptStatus),
        dispatch_phase=_enum(row, "dispatch_phase", DispatchPhase),
        item_status=_enum(row, "item_status", ItemStatus),
        delivery_status=_enum(row, "delivery_status", DeliveryStatus),
        delivery_state_version=_integer(row, "delivery_state_version", 1),
        item_state_version=_integer(row, "item_state_version", 1),
        next_attempt_at=None if definitive else _timestamp(row, "next_attempt_at"),
        delay_seconds=None if definitive else _bounded_integer(
            row, "delay_seconds", 5, 86400,
        ),
        resolved_at=_timestamp(row, "resolved_at") if definitive else None,
    )
    allowed_states = {
        ReconcileResult.STILL_UNKNOWN: (
            AttemptStatus.UNKNOWN, DispatchPhase.AMBIGUOUS,
            ItemStatus.RECONCILE_REQUIRED, {DeliveryStatus.RECONCILE_REQUIRED},
        ),
        ReconcileResult.ACCEPTED: (
            AttemptStatus.ACCEPTED, DispatchPhase.RECEIPT_RECORDED,
            ItemStatus.ACCEPTED,
            {DeliveryStatus.PENDING, DeliveryStatus.COMPLETED, DeliveryStatus.PARTIAL},
        ),
        ReconcileResult.REJECTED: (
            AttemptStatus.REJECTED, DispatchPhase.RECEIPT_RECORDED,
            ItemStatus.FAILED,
            {DeliveryStatus.PENDING, DeliveryStatus.PARTIAL, DeliveryStatus.FAILED},
        ),
    }
    attempt, phase, item, deliveries = allowed_states[result]
    if (
        receipt.attempt_status is not attempt or receipt.dispatch_phase is not phase
        or receipt.item_status is not item or receipt.delivery_status not in deliveries
    ):
        raise _fail("reconcile_result_state")
    return receipt
