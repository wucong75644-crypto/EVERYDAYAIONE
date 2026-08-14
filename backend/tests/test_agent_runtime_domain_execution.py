"""AR-05 lease、fencing 与 Accepted/Unknown 恢复测试。"""

from __future__ import annotations

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
    require_retry_safe,
)
from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    InvalidRecoveryError,
    LeaseExpiredError,
)
from services.agent.runtime.domain.execution import Lease
from services.agent.runtime.domain.identity import (
    ActionAttemptId,
    ActionId,
    FencingToken,
    IdempotencyKey,
    RunId,
)
from services.agent.runtime.domain.run import RunAttempt, RunAttemptOutcome
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind


NOW = datetime(2026, 7, 27, tzinfo=UTC)


def test_matching_live_fencing_token_is_accepted() -> None:
    lease = Lease(FencingToken("token-1"), NOW + timedelta(seconds=90))
    lease.validate(FencingToken("token-1"), NOW)


def test_mismatched_fencing_token_is_rejected() -> None:
    lease = Lease(FencingToken("token-1"), NOW + timedelta(seconds=90))
    with pytest.raises(FencingTokenMismatchError):
        lease.validate(FencingToken("stale-token"), NOW)


def test_expired_lease_is_rejected() -> None:
    lease = Lease(FencingToken("token-1"), NOW)
    with pytest.raises(LeaseExpiredError):
        lease.validate(FencingToken("token-1"), NOW)


def test_retry_safe_action_can_start_ordinary_retry() -> None:
    require_retry_safe(ActionStatus.RUNNING, RetryDisposition.RETRY_SAFE)


@pytest.mark.parametrize(
    "status",
    (ActionStatus.ACCEPTED, ActionStatus.UNKNOWN),
)
def test_accepted_or_unknown_action_must_reconcile(status: ActionStatus) -> None:
    with pytest.raises(InvalidRecoveryError, match="must reconcile"):
        require_retry_safe(status, RetryDisposition.RETRY_SAFE)


@pytest.mark.parametrize(
    "disposition",
    tuple(
        disposition
        for disposition in RetryDisposition
        if disposition is not RetryDisposition.RETRY_SAFE
    ),
)
def test_non_safe_disposition_cannot_start_ordinary_retry(
    disposition: RetryDisposition,
) -> None:
    with pytest.raises(InvalidRecoveryError):
        require_retry_safe(ActionStatus.RUNNING, disposition)


def test_completed_action_requires_result() -> None:
    result = ActionResult(
        action_id=ActionId("action-1"),
        scope=RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", None),
        status=ActionResultStatus.SUCCESS,
        result_hash="a" * 64,
    )
    require_action_result(ActionStatus.COMPLETED, result)
    with pytest.raises(ValueError, match="requires ActionResult"):
        require_action_result(ActionStatus.COMPLETED, None)


def test_failed_action_rejects_success_result() -> None:
    result = ActionResult(
        action_id=ActionId("action-1"),
        scope=RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", None),
        status=ActionResultStatus.SUCCESS,
        result_hash="a" * 64,
    )
    with pytest.raises(ValueError, match="requires an error"):
        require_action_result(ActionStatus.FAILED, result)


def _scope() -> RuntimeScope:
    return RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", None)


def _lease() -> Lease:
    return Lease(FencingToken("token-1"), NOW + timedelta(seconds=90))


@pytest.mark.parametrize("field_name", ("claimed_at", "ended_at"))
def test_run_attempt_rejects_naive_datetime(field_name: str) -> None:
    values = {"claimed_at": NOW, "ended_at": NOW}
    values[field_name] = datetime(2026, 7, 27)
    with pytest.raises(ValueError, match=f"{field_name} must be timezone-aware"):
        RunAttempt(
            run_id=RunId("run-1"),
            scope=_scope(),
            attempt_number=1,
            worker_id="worker-1",
            lease=_lease(),
            claimed_at=values["claimed_at"],
            ended_at=values["ended_at"],
            outcome=RunAttemptOutcome.COMPLETED,
        )


@pytest.mark.parametrize(
    "field_name",
    ("started_at", "accepted_at", "ended_at"),
)
def test_action_attempt_rejects_naive_datetime(field_name: str) -> None:
    values = {
        "started_at": NOW,
        "accepted_at": NOW,
        "ended_at": NOW,
    }
    values[field_name] = datetime(2026, 7, 27)
    with pytest.raises(ValueError, match=f"{field_name} must be timezone-aware"):
        ActionAttempt(
            attempt_id=ActionAttemptId("attempt-1"),
            action_id=ActionId("action-1"),
            scope=_scope(),
            attempt_number=1,
            status=ActionAttemptStatus.COMPLETED,
            worker_id="worker-1",
            idempotency_key=IdempotencyKey("action:1"),
            request_hash="b" * 64,
            lease=_lease(),
            started_at=values["started_at"],
            accepted_at=values["accepted_at"],
            ended_at=values["ended_at"],
        )
