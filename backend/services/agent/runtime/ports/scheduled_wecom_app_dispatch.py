"""Typed boundary for one Scheduled Runtime WeCom App dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol

from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DispatchOutcomeReceipt,
)
from services.wecom.app_outbound import WecomAppOutboundReceipt


class AppDispatchOutcome(StrEnum):
    ALREADY_PERSISTED = "already_persisted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class AppOutboundTransportPort(Protocol):
    async def send_typed(
        self,
        *,
        provider_request_id: str,
        target: str,
        payload: Mapping[str, Any],
    ) -> WecomAppOutboundReceipt: ...


@dataclass(frozen=True, kw_only=True)
class ScheduledWecomAppBinding:
    org_id: str
    corp_id: str
    agent_id: int
    transport: AppOutboundTransportPort = field(repr=False, compare=False)


@dataclass(frozen=True, kw_only=True)
class AppDispatchResult:
    outcome: AppDispatchOutcome
    intent_id: str
    item_id: str
    dispatch_receipt: DispatchOutcomeReceipt | None = None


class ScheduledWecomAppDispatchError(RuntimeError):
    """Stable failure-closed error before any WeCom App side effect."""
