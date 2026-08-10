"""Typed outcomes for one Runtime-owned Scheduled WeCom routing pass."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DispatchOutcomeReceipt,
    UnsupportedReason,
    UnsupportedTerminalizationReceipt,
    UnavailableReason,
)


class ScheduledWecomRouteOutcome(StrEnum):
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    CONFIG_UNAVAILABLE = "config_unavailable"
    UNSUPPORTED = "unsupported"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    ALREADY_PERSISTED = "already_persisted"


@dataclass(frozen=True, kw_only=True)
class ScheduledWecomRouteResult:
    outcome: ScheduledWecomRouteOutcome
    intent_id: str | None = None
    item_id: str | None = None
    unavailable_reason: UnavailableReason | None = None
    unsupported_reason: UnsupportedReason | None = None
    dispatch_receipt: DispatchOutcomeReceipt | None = None
    terminalization_receipt: UnsupportedTerminalizationReceipt | None = None
