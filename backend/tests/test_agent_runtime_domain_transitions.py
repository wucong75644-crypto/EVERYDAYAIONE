"""AR-05 闭合状态图的合法、非法与终态不可逆测试。"""

from __future__ import annotations

import pytest

from services.agent.runtime.domain.action import (
    ActionAttemptStatus,
    ActionStatus,
)
from services.agent.runtime.domain.errors import InvalidTransitionError
from services.agent.runtime.domain.model_step import ModelStepStatus
from services.agent.runtime.domain.run import RunStatus
from services.agent.runtime.domain.session import SessionStatus
from services.agent.runtime.domain.transitions import (
    ACTION_ATTEMPT_TRANSITIONS,
    ACTION_TRANSITIONS,
    MODEL_STEP_TRANSITIONS,
    RUN_TRANSITIONS,
    SESSION_TRANSITIONS,
    allowed_transitions,
    validate_transition,
)


STATE_MACHINES = (
    (SessionStatus, SESSION_TRANSITIONS),
    (RunStatus, RUN_TRANSITIONS),
    (ModelStepStatus, MODEL_STEP_TRANSITIONS),
    (ActionStatus, ACTION_TRANSITIONS),
    (ActionAttemptStatus, ACTION_ATTEMPT_TRANSITIONS),
)

LEGAL_TRANSITIONS = tuple(
    (current, target)
    for _, transitions in STATE_MACHINES
    for current, targets in transitions.items()
    for target in targets
)

ILLEGAL_TRANSITIONS = tuple(
    (current, target)
    for status_type, transitions in STATE_MACHINES
    for current in status_type
    for target in status_type
    if target not in transitions[current]
)


@pytest.mark.parametrize(("current", "target"), LEGAL_TRANSITIONS)
def test_every_declared_transition_is_accepted(current: object, target: object) -> None:
    validate_transition(current, target)  # type: ignore[type-var]


@pytest.mark.parametrize(("current", "target"), ILLEGAL_TRANSITIONS)
def test_every_undeclared_transition_is_rejected(
    current: object,
    target: object,
) -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition(current, target)  # type: ignore[type-var]


@pytest.mark.parametrize(
    "terminal",
    tuple(
        status
        for status_type, transitions in STATE_MACHINES
        for status in status_type
        if not transitions[status]
    ),
)
def test_terminal_statuses_have_no_outgoing_transition(terminal: object) -> None:
    assert not allowed_transitions(terminal)  # type: ignore[type-var]


def test_cross_machine_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition(RunStatus.RUNNING, ActionStatus.COMPLETED)  # type: ignore[arg-type]
