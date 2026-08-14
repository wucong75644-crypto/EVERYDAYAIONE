"""AR-12 Action result and frozen transition contracts."""

from datetime import UTC, datetime, timedelta

import pytest

from services.agent.runtime.domain.action import (
    ActionAttempt,
    ActionAttemptStatus,
    ActionResult,
    ActionResultStatus,
    ActionStatus,
    RetryDisposition,
    require_action_result,
)
from services.agent.runtime.domain.execution import Lease
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind
from services.agent.runtime.domain.transitions import validate_transition


SCOPE = RuntimeScope(
    org_id=None,
    user_id="11111111-1111-1111-1111-111111111111",
    kind=ScopeKind.USER,
    scope_id="11111111-1111-1111-1111-111111111111",
)


def _result(status: ActionResultStatus) -> ActionResult:
    return ActionResult(
        action_id="action-1", scope=SCOPE, status=status,
        result_hash="a" * 64,
    )


def test_completed_and_failed_require_matching_results() -> None:
    require_action_result(ActionStatus.COMPLETED, _result(ActionResultStatus.SUCCESS))
    require_action_result(ActionStatus.FAILED, _result(ActionResultStatus.ERROR))
    with pytest.raises(ValueError, match="requires ActionResult"):
        require_action_result(ActionStatus.FAILED, None)
    with pytest.raises(ValueError, match="error ActionResult"):
        require_action_result(
            ActionStatus.FAILED, _result(ActionResultStatus.SUCCESS),
        )
    with pytest.raises(ValueError, match="cannot use"):
        require_action_result(
            ActionStatus.COMPLETED, _result(ActionResultStatus.ERROR),
        )


def test_ar05_transition_edges_are_not_narrowed() -> None:
    validate_transition(ActionStatus.ACCEPTED, ActionStatus.UNKNOWN)
    validate_transition(
        ActionAttemptStatus.CLAIMED, ActionAttemptStatus.FAILED,
    )
    validate_transition(
        ActionAttemptStatus.CLAIMED, ActionAttemptStatus.CANCELLED,
    )


def test_claimed_attempt_can_be_terminal_before_dispatch() -> None:
    now = datetime.now(UTC)
    attempt = ActionAttempt(
        attempt_id="attempt-1", action_id="action-1", scope=SCOPE,
        attempt_number=1, status=ActionAttemptStatus.FAILED,
        worker_id="worker", idempotency_key="attempt-key",
        request_hash="b" * 64,
        lease=Lease("22222222-2222-2222-2222-222222222222",
                    now + timedelta(minutes=1)),
        started_at=now, ended_at=now,
    )
    assert attempt.status is ActionAttemptStatus.FAILED
    assert RetryDisposition.RETRY_SAFE.value == "retry_safe"
