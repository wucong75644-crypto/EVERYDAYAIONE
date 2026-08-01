"""Shared contracts for AR-17.3 specialist executors.

The contracts are deliberately provider-agnostic.  A provider adapter may do
I/O, but it cannot widen the action scope or turn an ambiguous submit into a
retryable failure.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt, RuntimeScope
from services.agent.runtime.executors.contracts import canonical_json


_SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|credential|api[_-]?key|authorization|cookie|private[_-]?key)",
    re.I,
)


class ProviderState(StrEnum):
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, kw_only=True)
class ProviderReceipt:
    """A provider fact; ``UNKNOWN`` is intentionally first-class."""

    state: ProviderState
    provider: str
    request_hash: str
    provider_task_ref: str | None = None
    status_locator: str | None = None
    callback_correlation: str | None = None
    result: Mapping[str, object] = field(default_factory=dict)
    cost: Mapping[str, object] = field(default_factory=dict)
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.request_hash) != 64:
            raise ValueError("SPECIALIST_REQUEST_HASH_INVALID")
        if self.state in {ProviderState.ACCEPTED, ProviderState.UNKNOWN}:
            if not self.evidence and not self.provider_task_ref:
                raise ValueError("SPECIALIST_AMBIGUITY_EVIDENCE_REQUIRED")
        if self.state is ProviderState.ACCEPTED and not self.provider_task_ref:
            raise ValueError("SPECIALIST_PROVIDER_REF_REQUIRED")


class SpecialistProvider(Protocol):
    async def submit(
        self, attempt: ActionAttempt, request: Mapping[str, object],
        *, idempotency_key: str,
    ) -> ProviderReceipt: ...

    async def reconcile(
        self, attempt: ActionAttempt, receipt: Mapping[str, object],
    ) -> ProviderReceipt: ...

    async def cancel(
        self, attempt: ActionAttempt, receipt: Mapping[str, object],
    ) -> ProviderReceipt: ...


@dataclass(frozen=True, kw_only=True)
class CapabilityGrant:
    action_id: str
    attempt_id: str
    executor_type: str
    executor_revision: int
    scope: RuntimeScope
    expires_at: datetime

    def assert_valid(self, attempt: ActionAttempt, executor_type: str, revision: int) -> None:
        if datetime.now(self.expires_at.tzinfo) >= self.expires_at:
            raise PermissionError("SPECIALIST_CAPABILITY_EXPIRED")
        if (self.action_id != str(attempt.action_id)
                or self.attempt_id != str(attempt.attempt_id)
                or self.executor_type != executor_type
                or self.executor_revision != revision
                or self.scope != attempt.scope):
            raise PermissionError("SPECIALIST_CAPABILITY_BINDING_MISMATCH")


@dataclass(frozen=True, kw_only=True)
class NetworkRule:
    provider: str
    method: str
    paths: frozenset[str]
    max_response_bytes: int = 2_000_000
    allow_redirects: bool = False

    def allows(self, provider: str, method: str, path: str) -> bool:
        return (provider == self.provider and method.upper() == self.method.upper()
                and path in self.paths)

    def assert_allowed(self, provider: str, method: str, path: str) -> None:
        if not self.allows(provider, method, path):
            raise PermissionError("SPECIALIST_NETWORK_NOT_ALLOWED")


def action_idempotency_key(attempt: ActionAttempt, operation: str) -> str:
    """Stable key used by provider adapters and reconciliation RPCs."""
    raw = f"agent-action:{attempt.action_id}:{operation}:v1"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_public_request(value: object, key: str = "") -> None:
    """Reject secret material; only opaque ``*_handle`` values cross the SPI."""
    if key and _SENSITIVE_KEY.search(key) and not key.lower().endswith("_handle"):
        raise PermissionError("SPECIALIST_SECRET_HANDLE_REQUIRED")
    if isinstance(value, Mapping):
        for name, item in value.items():
            validate_public_request(item, str(name))
    elif isinstance(value, list):
        for item in value:
            validate_public_request(item)


def receipt_facts(receipt: ProviderReceipt) -> dict[str, object]:
    facts = {
        "provider": receipt.provider,
        "provider_task_ref": receipt.provider_task_ref,
        "status_locator": receipt.status_locator,
        "callback_correlation": receipt.callback_correlation,
        "request_hash": receipt.request_hash,
        "state": receipt.state.value,
        "evidence": dict(receipt.evidence),
    }
    if receipt.cost:
        facts["cost"] = dict(receipt.cost)
    if receipt.result:
        facts["result_hash"] = hashlib.sha256(
            canonical_json(receipt.result).encode("utf-8")
        ).hexdigest()
    return facts


@dataclass(frozen=True, kw_only=True)
class CostReservation:
    action_id: str
    attempt_id: str
    kind: str
    reserved_amount: int
    currency: str = "credits"

    def __post_init__(self) -> None:
        if self.kind not in {"reserve", "settle", "release", "refund", "adjustment"}:
            raise ValueError("SPECIALIST_COST_KIND_INVALID")
        if self.reserved_amount < 0:
            raise ValueError("SPECIALIST_COST_NEGATIVE")
