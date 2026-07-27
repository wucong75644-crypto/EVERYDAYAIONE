"""Authorization recovery and fenced dispatch persistence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.executors.types import ExecutorDescriptor
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
)


class DispatchGateOutcome(StrEnum):
    AUTHORIZED = "dispatch_authorized"
    ALREADY_AUTHORIZED = "already_authorized"


class DispatchGateDenied(RuntimeError):
    """The RPC closed this pre-gate Action for a permanent authorization fact."""


@dataclass(frozen=True, kw_only=True)
class DispatchGateReceipt:
    outcome: DispatchGateOutcome
    intent_id: str
    state_version: int
    external_idempotency_key: str
    recovery_mode: str


@dataclass(frozen=True, kw_only=True)
class AuthorizationRecoveryClaim:
    interaction_id: str
    recovery_token: str
    state_version: int
    lease_expires_at: datetime
    action: Mapping[str, object]
    grant: Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class PolicyReceiptRecord:
    receipt_id: str


class ActionAuthorizationPort(Protocol):
    async def gate(
        self, *, snapshot: ActionDispatchSnapshot,
        descriptor: ExecutorDescriptor,
    ) -> DispatchGateReceipt: ...

    async def claim_recovery(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> AuthorizationRecoveryClaim | None: ...

    async def record_allow_receipt(
        self, *, claim: AuthorizationRecoveryClaim,
        descriptor: ExecutorDescriptor, policy_revision: str,
        reason_codes: tuple[str, ...], obligations: tuple[str, ...],
        receipt_hash: str,
    ) -> PolicyReceiptRecord: ...

    async def activate(
        self, *, claim: AuthorizationRecoveryClaim,
        receipt: PolicyReceiptRecord,
    ) -> None: ...
