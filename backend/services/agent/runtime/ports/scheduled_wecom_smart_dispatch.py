"""Typed boundary for one Scheduled Runtime Smart Robot dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DispatchOutcomeReceipt,
)
from services.wecom.ws_outbound import WecomOutboundAckResult


class SmartRobotDispatchOutcome(StrEnum):
    ALREADY_PERSISTED = "already_persisted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class SmartRobotDispatchResult:
    outcome: SmartRobotDispatchOutcome
    intent_id: str
    item_id: str
    dispatch_receipt: DispatchOutcomeReceipt | None = None


class ScheduledWecomSmartDispatchError(RuntimeError):
    """Stable failure-closed error before any Smart Robot side effect."""


class SmartRobotProactiveTransportPort(Protocol):
    org_id: str
    is_connected: bool

    async def send_proactive_typed(
        self,
        provider_request_id: str,
        chatid: str,
        msgtype: str,
        content: dict[str, str],
    ) -> WecomOutboundAckResult: ...


class SmartRobotTransportResolverPort(Protocol):
    async def resolve_smart_transport(
        self, org_id: str,
    ) -> SmartRobotProactiveTransportPort | None: ...


class SmartRobotReadbackTransportPort(Protocol):
    org_id: str

    def lookup_outbound_result(
        self, provider_request_id: str,
    ) -> WecomOutboundAckResult | None: ...


class SmartRobotReadbackResolverPort(Protocol):
    async def resolve_smart_readback(
        self, org_id: str,
    ) -> SmartRobotReadbackTransportPort | None: ...
