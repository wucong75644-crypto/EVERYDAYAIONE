"""Policy input, decision, and persisted receipt types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from typing import Mapping

from services.agent.runtime.domain.identity import require_stable_value
from services.agent.runtime.executors.types import ExecutorDescriptor


class PolicyDecisionKind(StrEnum):
    ALLOW = "allow"
    REQUIRE_AUTHORIZATION = "require_authorization"
    DENY = "deny"


class PermissionMode(StrEnum):
    AUTO = "auto"
    ASK = "ask"
    PLAN = "plan"


@dataclass(frozen=True, kw_only=True)
class AuthorizationEvidence:
    explicit_intent: bool = False
    action_grant_id: str | None = None
    workflow_grant_id: str | None = None

    def __post_init__(self) -> None:
        if self.action_grant_id and self.workflow_grant_id:
            raise ValueError("authorization evidence must use one grant")


@dataclass(frozen=True, kw_only=True)
class PolicyContext:
    action_id: str
    run_id: str
    session_id: str
    actor_id: str
    org_id: str | None
    action_kind: str
    arguments_hash: str
    permission_mode: PermissionMode
    entitled: bool
    resource_scope_valid: bool
    descriptor: ExecutorDescriptor
    evidence: AuthorizationEvidence = field(
        default_factory=AuthorizationEvidence,
    )
    effective_scope: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.action_id, "action_id"),
            (self.run_id, "run_id"),
            (self.session_id, "session_id"),
            (self.actor_id, "actor_id"),
            (self.action_kind, "action_kind"),
            (self.arguments_hash, "arguments_hash"),
        ):
            require_stable_value(value, name)
        if len(self.arguments_hash) != 64:
            raise ValueError("arguments_hash must be SHA-256")
        if self.action_kind not in self.descriptor.action_kinds:
            raise ValueError("action kind does not match descriptor")


@dataclass(frozen=True, kw_only=True)
class PolicyDecision:
    decision: PolicyDecisionKind
    reason_codes: tuple[str, ...]
    obligations: tuple[str, ...] = ()
    grant_id: str | None = None

    def __post_init__(self) -> None:
        if not self.reason_codes:
            raise ValueError("policy decision requires reason codes")
        if self.decision is not PolicyDecisionKind.ALLOW and self.grant_id:
            raise ValueError("only allow decisions may bind a grant")


@dataclass(frozen=True, kw_only=True)
class PolicyReceipt:
    receipt_id: str
    action_id: str
    decision: PolicyDecisionKind
    arguments_hash: str
    executor_type: str
    executor_revision: int
    policy_revision: str
    effective_scope: Mapping[str, object]
    reason_codes: tuple[str, ...]
    obligations: tuple[str, ...]
    evaluated_at: datetime
    expires_at: datetime
    grant_id: str | None = None
    receipt_hash: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.receipt_id, "receipt_id"),
            (self.action_id, "action_id"),
            (self.executor_type, "executor_type"),
            (self.policy_revision, "policy_revision"),
        ):
            require_stable_value(value, name)
        if self.evaluated_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("receipt timestamps must be timezone-aware")
        if self.expires_at <= self.evaluated_at:
            raise ValueError("receipt expiry must follow evaluation")
        canonical_hash = self.canonical_hash()
        if not self.receipt_hash:
            object.__setattr__(self, "receipt_hash", canonical_hash)
        elif self.receipt_hash != canonical_hash:
            raise ValueError("receipt_hash does not match receipt facts")

    def canonical_hash(self) -> str:
        facts = {
            "action_id": self.action_id,
            "arguments_hash": self.arguments_hash,
            "decision": self.decision.value,
            "effective_scope": self.effective_scope,
            "evaluated_at": self.evaluated_at.isoformat(),
            "executor_revision": self.executor_revision,
            "executor_type": self.executor_type,
            "expires_at": self.expires_at.isoformat(),
            "grant_id": self.grant_id,
            "obligations": self.obligations,
            "policy_revision": self.policy_revision,
            "reason_codes": self.reason_codes,
        }
        canonical = json.dumps(
            facts, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()
