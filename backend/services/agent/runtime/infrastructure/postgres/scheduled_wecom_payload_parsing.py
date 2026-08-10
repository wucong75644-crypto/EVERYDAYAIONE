"""Strict 227_46/227_47/227_49 Scheduled WeCom payload receipt parsers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import re
import unicodedata
from typing import Any, Mapping, TypeVar
from uuid import UUID

from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DeliveryStatus,
    DispatchChannel,
    DispatchPayload,
    DispatchPayloadOutcome,
    DispatchPayloadReadback,
    DispatchTarget,
    ItemStatus,
    UnavailableDispatchPayload,
    UnavailableReason,
    UnsupportedDispatchPayload,
    UnsupportedReason,
    UnsupportedTerminalizationOutcome,
    UnsupportedTerminalizationReceipt,
    WecomAppDispatchTarget,
    WecomSmartRobotDispatchTarget,
)


EnumT = TypeVar("EnumT", bound=StrEnum)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_KEYS = {
    "outcome", "payload_revision", "scheduled_run_id", "intent_id", "item_id",
    "item_key", "ordinal", "item_kind", "source_role", "source_revision",
    "source_identity_hash", "content_identity_hash", "result_hash", "target_hash",
    "channel", "target", "provider_revision", "delivery_state_version",
    "item_state_version", "message_type", "text", "payload_hash",
}
_TERMINALIZATION_KEYS = {
    "outcome", "request_id", "intent_id", "item_id", "reason_code", "item_status",
    "delivery_status", "delivery_state_version", "item_state_version", "terminalized_at",
}


def _fail(code: str) -> PersistenceContractError:
    return PersistenceContractError(f"SCHEDULED_WECOM_PAYLOAD_CONTRACT_INVALID:{code}")


def _row(raw: object, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != keys:
        raise _fail(code)
    return raw


def _minimal(raw: object, outcome: str) -> bool:
    return isinstance(raw, Mapping) and raw == {"outcome": outcome}


def _text(row: Mapping[str, Any], field: str, *, maximum: int = 500) -> str:
    value = row.get(field)
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > maximum or any(_invalid_transport_char(char) for char in value)
    ):
        raise _fail(field)
    return value


def _invalid_transport_char(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.category(char) in {"Cc", "Cs"}
        or 0xFDD0 <= codepoint <= 0xFDEF
        or (codepoint & 0xFFFF) in {0xFFFE, 0xFFFF}
    )


def _uuid(row: Mapping[str, Any], field: str) -> str:
    try:
        return str(UUID(_text(row, field, maximum=36)))
    except ValueError as exc:
        raise _fail(field) from exc


def _integer(row: Mapping[str, Any], field: str, minimum: int) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(field)
    return value


def _exact_integer(row: Mapping[str, Any], field: str, expected: int) -> int:
    value = _integer(row, field, expected)
    if value != expected:
        raise _fail(field)
    return value


def _payload_revision(row: Mapping[str, Any]) -> int:
    value = _integer(row, "payload_revision", 1)
    if value not in {1, 2}:
        raise _fail("payload_revision")
    return value


def _hash(row: Mapping[str, Any], field: str) -> str:
    value = _text(row, field, maximum=64)
    if not _HASH.fullmatch(value):
        raise _fail(field)
    return value


def _enum(row: Mapping[str, Any], field: str, kind: type[EnumT]) -> EnumT:
    try:
        return kind(_text(row, field, maximum=80))
    except ValueError as exc:
        raise _fail(field) from exc


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


def _target(row: Mapping[str, Any], channel: DispatchChannel) -> DispatchTarget:
    raw = row.get("target")
    if channel is DispatchChannel.APP:
        target = _row(raw, {"org_id", "corp_id", "wecom_userid"}, "target")
        return WecomAppDispatchTarget(
            org_id=_uuid(target, "org_id"),
            corp_id=_text(target, "corp_id", maximum=64),
            wecom_userid=_text(target, "wecom_userid", maximum=64),
        )
    target = _row(raw, {"org_id", "chatid"}, "target")
    return WecomSmartRobotDispatchTarget(
        org_id=_uuid(target, "org_id"),
        chatid=_text(target, "chatid", maximum=128),
    )


def parse_dispatch_payload(raw: object) -> DispatchPayloadReadback | None:
    """Parse an exact v1 rollback or v2 current payload outcome."""
    if _minimal(raw, "not_found"):
        return None
    if _minimal(raw, "fenced"):
        raise _fail("dispatch_payload_fenced")
    if isinstance(raw, Mapping) and raw.get("outcome") in {"unsupported", "unavailable"}:
        row = _row(raw, {"outcome", "reason_code"}, "dispatch_payload_outcome")
        outcome = _enum(row, "outcome", DispatchPayloadOutcome)
        if outcome is DispatchPayloadOutcome.UNSUPPORTED:
            return UnsupportedDispatchPayload(
                outcome=outcome, reason=_enum(row, "reason_code", UnsupportedReason),
            )
        return UnavailableDispatchPayload(
            outcome=outcome, reason=_enum(row, "reason_code", UnavailableReason),
        )
    row = _row(raw, _PAYLOAD_KEYS, "dispatch_payload")
    outcome = _enum(row, "outcome", DispatchPayloadOutcome)
    if outcome is not DispatchPayloadOutcome.PAYLOAD:
        raise _fail("dispatch_payload_outcome")
    channel = _enum(row, "channel", DispatchChannel)
    item_kind = _text(row, "item_kind", maximum=32)
    source_role = _text(row, "source_role", maximum=32)
    message_type = _text(row, "message_type", maximum=32)
    if (item_kind, source_role, message_type) != ("text", "text", "text"):
        raise _fail("dispatch_payload_kind")
    return DispatchPayload(
        outcome=outcome, payload_revision=_payload_revision(row),
        scheduled_run_id=_uuid(row, "scheduled_run_id"), intent_id=_uuid(row, "intent_id"),
        item_id=_uuid(row, "item_id"), item_key=_hash(row, "item_key"),
        ordinal=_integer(row, "ordinal", 1), item_kind=item_kind, source_role=source_role,
        source_revision=_exact_integer(row, "source_revision", 1),
        source_identity_hash=_hash(row, "source_identity_hash"),
        content_identity_hash=_hash(row, "content_identity_hash"),
        result_hash=_hash(row, "result_hash"), target_hash=_hash(row, "target_hash"),
        channel=channel, target=_target(row, channel),
        provider_revision=_integer(row, "provider_revision", 1),
        delivery_state_version=_integer(row, "delivery_state_version", 1),
        item_state_version=_integer(row, "item_state_version", 0),
        message_type=message_type, text=_text(row, "text", maximum=500),
        payload_hash=_hash(row, "payload_hash"),
    )


def parse_unsupported_terminalization(raw: object) -> UnsupportedTerminalizationReceipt:
    """Parse one exact 227_47 terminalized/readback receipt."""
    if _minimal(raw, "fenced"):
        raise _fail("unsupported_fenced")
    if _minimal(raw, "not_found"):
        raise _fail("unsupported_not_found")
    row = _row(raw, _TERMINALIZATION_KEYS, "unsupported_terminalization")
    item_status = _enum(row, "item_status", ItemStatus)
    delivery_status = _enum(row, "delivery_status", DeliveryStatus)
    if item_status is not ItemStatus.CANCELLED or delivery_status not in {
        DeliveryStatus.PENDING, DeliveryStatus.PARTIAL, DeliveryStatus.FAILED,
    }:
        raise _fail("unsupported_terminalization_state")
    return UnsupportedTerminalizationReceipt(
        outcome=_enum(row, "outcome", UnsupportedTerminalizationOutcome),
        request_id=_uuid(row, "request_id"), intent_id=_uuid(row, "intent_id"),
        item_id=_uuid(row, "item_id"), reason=_enum(row, "reason_code", UnsupportedReason),
        item_status=item_status, delivery_status=delivery_status,
        delivery_state_version=_integer(row, "delivery_state_version", 1),
        item_state_version=_integer(row, "item_state_version", 1),
        terminalized_at=_timestamp(row, "terminalized_at"),
    )
