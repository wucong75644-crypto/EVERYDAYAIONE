"""闭合状态图与统一转移验证。"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar, cast

from services.agent.runtime.domain.action import (
    ActionAttemptStatus,
    ActionStatus,
)
from services.agent.runtime.domain.errors import InvalidTransitionError
from services.agent.runtime.domain.model_step import ModelStepStatus
from services.agent.runtime.domain.run import RunStatus
from services.agent.runtime.domain.session import SessionStatus


StatusT = TypeVar("StatusT", bound=StrEnum)


SESSION_TRANSITIONS = {
    SessionStatus.IDLE: frozenset({SessionStatus.CLAIMED}),
    SessionStatus.CLAIMED: frozenset({
        SessionStatus.HYDRATING,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.HYDRATING: frozenset({
        SessionStatus.READY,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.READY: frozenset({
        SessionStatus.SAMPLING,
        SessionStatus.COMMITTING,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.SAMPLING: frozenset({
        SessionStatus.EXECUTING_ACTIONS,
        SessionStatus.COMPACTING,
        SessionStatus.AUTH_RECOVERY,
        SessionStatus.COMMITTING,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.EXECUTING_ACTIONS: frozenset({
        SessionStatus.SAMPLING,
        SessionStatus.WAITING_EXTERNAL,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.COMPACTING: frozenset({
        SessionStatus.SAMPLING,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.AUTH_RECOVERY: frozenset({
        SessionStatus.SAMPLING,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.WAITING_EXTERNAL: frozenset({
        SessionStatus.CLAIMED,
        SessionStatus.CANCELLING,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.COMMITTING: frozenset({
        SessionStatus.COMPLETED,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.CANCELLING: frozenset({
        SessionStatus.CANCELLED,
        SessionStatus.OWNERSHIP_LOST,
        SessionStatus.FAILED,
    }),
    SessionStatus.COMPLETED: frozenset(),
    SessionStatus.CANCELLED: frozenset(),
    SessionStatus.OWNERSHIP_LOST: frozenset(),
    SessionStatus.FAILED: frozenset(),
}

RUN_TRANSITIONS = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset({
        RunStatus.WAITING_ACTIONS,
        RunStatus.WAITING_INTERACTION,
        RunStatus.PAUSED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }),
    RunStatus.WAITING_ACTIONS: frozenset({
        RunStatus.QUEUED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }),
    RunStatus.WAITING_INTERACTION: frozenset({
        RunStatus.QUEUED,
        RunStatus.PAUSED,
        RunStatus.CANCELLED,
    }),
    RunStatus.PAUSED: frozenset({RunStatus.QUEUED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

MODEL_STEP_TRANSITIONS = {
    ModelStepStatus.PENDING: frozenset({
        ModelStepStatus.RUNNING,
        ModelStepStatus.CANCELLED,
    }),
    ModelStepStatus.RUNNING: frozenset({
        ModelStepStatus.COMPLETED,
        ModelStepStatus.FAILED,
        ModelStepStatus.CANCELLED,
    }),
    ModelStepStatus.COMPLETED: frozenset(),
    ModelStepStatus.FAILED: frozenset(),
    ModelStepStatus.CANCELLED: frozenset(),
}

ACTION_TRANSITIONS = {
    ActionStatus.REQUESTED: frozenset({
        ActionStatus.AWAITING_AUTHORIZATION,
        ActionStatus.QUEUED,
        ActionStatus.REJECTED,
    }),
    ActionStatus.AWAITING_AUTHORIZATION: frozenset({
        ActionStatus.QUEUED,
        ActionStatus.REJECTED,
        ActionStatus.CANCELLED,
    }),
    ActionStatus.QUEUED: frozenset({
        ActionStatus.RUNNING,
        ActionStatus.CANCELLED,
    }),
    ActionStatus.RUNNING: frozenset({
        ActionStatus.COMPLETED,
        ActionStatus.ACCEPTED,
        ActionStatus.FAILED,
        ActionStatus.UNKNOWN,
        ActionStatus.CANCELLED,
    }),
    ActionStatus.ACCEPTED: frozenset({
        ActionStatus.COMPLETED,
        ActionStatus.FAILED,
        ActionStatus.UNKNOWN,
        ActionStatus.CANCELLED,
    }),
    ActionStatus.UNKNOWN: frozenset({
        ActionStatus.COMPLETED,
        ActionStatus.FAILED,
        ActionStatus.CANCELLED,
    }),
    ActionStatus.COMPLETED: frozenset(),
    ActionStatus.FAILED: frozenset(),
    ActionStatus.REJECTED: frozenset(),
    ActionStatus.CANCELLED: frozenset(),
}

ACTION_ATTEMPT_TRANSITIONS = {
    ActionAttemptStatus.CLAIMED: frozenset({
        ActionAttemptStatus.DISPATCHING,
        ActionAttemptStatus.FAILED,
        ActionAttemptStatus.CANCELLED,
    }),
    ActionAttemptStatus.DISPATCHING: frozenset({
        ActionAttemptStatus.ACCEPTED,
        ActionAttemptStatus.COMPLETED,
        ActionAttemptStatus.FAILED,
        ActionAttemptStatus.UNKNOWN,
        ActionAttemptStatus.CANCELLED,
    }),
    ActionAttemptStatus.ACCEPTED: frozenset({
        ActionAttemptStatus.COMPLETED,
        ActionAttemptStatus.FAILED,
        ActionAttemptStatus.UNKNOWN,
        ActionAttemptStatus.CANCELLED,
    }),
    ActionAttemptStatus.UNKNOWN: frozenset({
        ActionAttemptStatus.COMPLETED,
        ActionAttemptStatus.FAILED,
        ActionAttemptStatus.CANCELLED,
    }),
    ActionAttemptStatus.COMPLETED: frozenset(),
    ActionAttemptStatus.FAILED: frozenset(),
    ActionAttemptStatus.CANCELLED: frozenset(),
}

_TRANSITION_MAPS = {
    SessionStatus: SESSION_TRANSITIONS,
    RunStatus: RUN_TRANSITIONS,
    ModelStepStatus: MODEL_STEP_TRANSITIONS,
    ActionStatus: ACTION_TRANSITIONS,
    ActionAttemptStatus: ACTION_ATTEMPT_TRANSITIONS,
}


def allowed_transitions(current: StatusT) -> frozenset[StatusT]:
    """返回同一闭合状态机中 current 的全部合法目标。"""
    transitions = _TRANSITION_MAPS.get(type(current))
    if transitions is None:
        raise TypeError(f"unsupported status type: {type(current).__name__}")
    return cast(frozenset[StatusT], transitions[current])


def validate_transition(current: StatusT, target: StatusT) -> None:
    """非法、跨状态机、同状态和终态反转均失败关闭。"""
    if type(current) is not type(target) or target not in allowed_transitions(current):
        raise InvalidTransitionError(
            f"invalid transition: {current.value} -> {target.value}"
        )
