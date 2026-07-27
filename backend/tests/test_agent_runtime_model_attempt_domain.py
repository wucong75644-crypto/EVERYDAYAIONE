"""Closed ModelAttempt domain contract."""

import pytest

from services.agent.runtime.domain.errors import InvalidTransitionError
from services.agent.runtime.domain.model_attempt import (
    ModelAttemptStatus,
    allowed_model_attempt_transitions,
    validate_model_attempt_transition,
)


def test_model_attempt_transition_graph_is_closed() -> None:
    assert allowed_model_attempt_transitions(
        ModelAttemptStatus.PREPARED,
    ) == {
        ModelAttemptStatus.DISPATCHING,
        ModelAttemptStatus.FAILED,
        ModelAttemptStatus.CANCELLED,
    }
    assert allowed_model_attempt_transitions(
        ModelAttemptStatus.UNKNOWN,
    ) == {
        ModelAttemptStatus.COMPLETED,
        ModelAttemptStatus.FAILED,
        ModelAttemptStatus.CANCELLED,
    }
    for terminal in (
        ModelAttemptStatus.COMPLETED,
        ModelAttemptStatus.FAILED,
        ModelAttemptStatus.CANCELLED,
    ):
        assert allowed_model_attempt_transitions(terminal) == set()


def test_same_state_and_terminal_reversal_are_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        validate_model_attempt_transition(
            ModelAttemptStatus.UNKNOWN,
            ModelAttemptStatus.UNKNOWN,
        )
    with pytest.raises(InvalidTransitionError):
        validate_model_attempt_transition(
            ModelAttemptStatus.COMPLETED,
            ModelAttemptStatus.FAILED,
        )
