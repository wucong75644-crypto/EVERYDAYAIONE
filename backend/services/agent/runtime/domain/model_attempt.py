"""ModelAttempt 的闭合生命周期与恢复分类。"""

from __future__ import annotations

from enum import StrEnum

from services.agent.runtime.domain.errors import InvalidTransitionError


class ModelAttemptStatus(StrEnum):
    PREPARED = "prepared"
    DISPATCHING = "dispatching"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class ModelDispatchPhase(StrEnum):
    PREPARED = "prepared"
    REQUEST_STARTED = "request_started"
    RESPONSE_STARTED = "response_started"


class ModelRetryDisposition(StrEnum):
    FORBIDDEN = "forbidden"
    RECONCILE_ONLY = "reconcile_only"
    RETRY_SAFE = "retry_safe"


class ModelLateOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


MODEL_ATTEMPT_TRANSITIONS = {
    ModelAttemptStatus.PREPARED: frozenset({
        ModelAttemptStatus.DISPATCHING,
        ModelAttemptStatus.FAILED,
        ModelAttemptStatus.CANCELLED,
    }),
    ModelAttemptStatus.DISPATCHING: frozenset({
        ModelAttemptStatus.COMPLETED,
        ModelAttemptStatus.FAILED,
        ModelAttemptStatus.UNKNOWN,
        ModelAttemptStatus.CANCELLED,
    }),
    ModelAttemptStatus.UNKNOWN: frozenset({
        ModelAttemptStatus.COMPLETED,
        ModelAttemptStatus.FAILED,
        ModelAttemptStatus.CANCELLED,
    }),
    ModelAttemptStatus.COMPLETED: frozenset(),
    ModelAttemptStatus.FAILED: frozenset(),
    ModelAttemptStatus.CANCELLED: frozenset(),
}


def allowed_model_attempt_transitions(
    current: ModelAttemptStatus,
) -> frozenset[ModelAttemptStatus]:
    return MODEL_ATTEMPT_TRANSITIONS[current]


def validate_model_attempt_transition(
    current: ModelAttemptStatus,
    target: ModelAttemptStatus,
) -> None:
    if target not in allowed_model_attempt_transitions(current):
        raise InvalidTransitionError(
            f"invalid ModelAttempt transition: {current.value} -> {target.value}"
        )
