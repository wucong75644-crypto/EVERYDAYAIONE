"""Action、ActionAttempt、结果与恢复合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from services.agent.runtime.domain.errors import InvalidRecoveryError
from services.agent.runtime.domain.execution import Lease, require_aware_datetime
from services.agent.runtime.domain.identity import (
    ActionAttemptId,
    ActionId,
    IdempotencyKey,
    require_stable_value,
)
from services.agent.runtime.domain.scope import RuntimeScope


def _require_sha256(value: str, name: str) -> None:
    require_stable_value(value, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hash")


class ActionStatus(StrEnum):
    REQUESTED = "requested"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    QUEUED = "queued"
    RUNNING = "running"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ActionAttemptStatus(StrEnum):
    CLAIMED = "claimed"
    DISPATCHING = "dispatching"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class RetryDisposition(StrEnum):
    RETRY_SAFE = "retry_safe"
    RETRY_AFTER_RECONCILE = "retry_after_reconcile"
    RETRY_REQUIRES_USER = "retry_requires_user"
    NON_RETRYABLE = "non_retryable"
    COMPENSATE = "compensate"


class ActionResultStatus(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass(frozen=True)
class ActionAttempt:
    """一次真实 Executor/Provider 调度。"""

    attempt_id: ActionAttemptId
    action_id: ActionId
    scope: RuntimeScope
    attempt_number: int
    status: ActionAttemptStatus
    worker_id: str
    idempotency_key: IdempotencyKey
    request_hash: str
    lease: Lease
    started_at: datetime
    accepted_at: datetime | None = None
    ended_at: datetime | None = None
    session_id: str | None = None
    run_id: str | None = None
    external_receipt: Mapping[str, object] = field(default_factory=dict)
    ambiguity_evidence: Mapping[str, object] = field(default_factory=dict)
    capabilities: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.attempt_id, "attempt_id"),
            (self.action_id, "action_id"),
            (self.worker_id, "worker_id"),
            (self.idempotency_key, "idempotency_key"),
        ):
            require_stable_value(value, name)
        _require_sha256(self.request_hash, "request_hash")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        require_aware_datetime(self.started_at, "started_at")
        require_aware_datetime(self.accepted_at, "accepted_at")
        require_aware_datetime(self.ended_at, "ended_at")
        if self.session_id is not None:
            require_stable_value(self.session_id, "session_id")
        if self.run_id is not None:
            require_stable_value(self.run_id, "run_id")
        if self.status is ActionAttemptStatus.ACCEPTED:
            if self.accepted_at is None or not self.external_receipt:
                raise ValueError(
                    "accepted attempt requires accepted_at and external_receipt"
                )
        if self.status is ActionAttemptStatus.UNKNOWN and not self.ambiguity_evidence:
            raise ValueError("unknown attempt requires ambiguity_evidence")
        terminal = {
            ActionAttemptStatus.COMPLETED,
            ActionAttemptStatus.FAILED,
            ActionAttemptStatus.CANCELLED,
        }
        if (self.status in terminal) != (self.ended_at is not None):
            raise ValueError("terminal attempt requires ended_at")


@dataclass(frozen=True)
class ActionResult:
    """Action 的规范化一对一结果。"""

    action_id: ActionId
    scope: RuntimeScope
    status: ActionResultStatus
    result_hash: str
    summary: str = ""
    data: Mapping[str, object] | None = None
    artifact_ids: tuple[str, ...] = ()
    usage: Mapping[str, object] = field(default_factory=dict)
    cost: Mapping[str, object] = field(default_factory=dict)
    receipt: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_stable_value(self.action_id, "action_id")
        _require_sha256(self.result_hash, "result_hash")


def require_retry_safe(
    status: ActionStatus,
    disposition: RetryDisposition,
) -> None:
    """仅确认安全且未进入外部不确定阶段的 Action 可普通重试。"""
    if status in (ActionStatus.ACCEPTED, ActionStatus.UNKNOWN):
        raise InvalidRecoveryError(
            f"{status.value} action must reconcile before retry"
        )
    if disposition is not RetryDisposition.RETRY_SAFE:
        raise InvalidRecoveryError(
            f"{disposition.value} does not permit an ordinary retry"
        )


def require_action_result(
    target: ActionStatus,
    result: ActionResult | None,
) -> None:
    """Action completed/failed 必须有规范结果，其他状态不得提前绑定。"""
    result_targets = {ActionStatus.COMPLETED, ActionStatus.FAILED}
    if target in result_targets and result is None:
        raise ValueError(f"{target.value} action requires ActionResult")
    if target not in result_targets and result is not None:
        raise ValueError("ActionResult is only valid for completed/failed action")
    if (
        result is not None
        and target is ActionStatus.FAILED
        and result.status is not ActionResultStatus.ERROR
    ):
        raise ValueError("failed action requires an error ActionResult")
    if (
        result is not None
        and target is ActionStatus.COMPLETED
        and result.status is ActionResultStatus.ERROR
    ):
        raise ValueError("completed action cannot use an error ActionResult")
