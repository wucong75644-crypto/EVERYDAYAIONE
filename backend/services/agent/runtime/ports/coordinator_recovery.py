"""Private persistence boundary for AR-14 recovery orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol


class RecoveryOutcome(StrEnum):
    CLAIMED = "claimed"
    FOUND = "found"
    NOT_FOUND = "not_found"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    RUN_CANCELLED_USE_LATE_RECEIPT = "run_cancelled_use_late_receipt"
    APPLIED = "applied"
    CONFIRMED = "confirmed"
    OWNERSHIP_LOST = "ownership_lost"


class ActionRecoveryOperation(StrEnum):
    RECONCILE = "reconcile"
    CANCEL = "cancel"


@dataclass(frozen=True, kw_only=True)
class RunRecoveryClaim:
    outcome: RecoveryOutcome
    run_id: str | None = None
    execution_token: str | None = None
    state_version: int | None = None


@dataclass(frozen=True, kw_only=True)
class RunAggregateSnapshot:
    run: Mapping[str, object]
    latest_model_step: Mapping[str, object] | None
    unresolved_model_attempt: Mapping[str, object] | None
    latest_model_result: Mapping[str, object] | None
    model_steps: tuple[Mapping[str, object], ...]
    actions: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, kw_only=True)
class ActionDispatchSnapshot:
    attempt: Mapping[str, object]
    action: Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class ActionRecoveryClaim:
    outcome: RecoveryOutcome
    operation: ActionRecoveryOperation | None = None
    parent_run_id: str | None = None
    parent_run_status: str | None = None
    parent_run_state_version: int | None = None
    attempt_id: str | None = None
    execution_token: str | None = None
    state_version: int | None = None
    lease_expires_at: datetime | None = None
    snapshot: ActionDispatchSnapshot | None = None


@dataclass(frozen=True, kw_only=True)
class ChildCancelRecoveryClaim:
    outcome: RecoveryOutcome
    intent_id: str | None = None
    claim_token: str | None = None
    state_version: int | None = None


@dataclass(frozen=True, kw_only=True)
class ModelResultDraft:
    output_kind: str
    content_hash: str
    text_content: str | None = None
    structured_content: object | None = None
    schema_revision: str | None = None


class CoordinatorRecoveryPort(Protocol):
    async def claim_next_run(
        self, *, worker_id: str, lease_seconds: int = 90,
        max_attempts: int = 3,
    ) -> RunRecoveryClaim: ...

    async def get_run_aggregate(
        self, *, run_id: str, worker_id: str, execution_token: str,
    ) -> RunAggregateSnapshot: ...

    async def complete_model_with_result(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, response_receipt: Mapping[str, object],
        response_hash: str, stop_reason: str,
        provider_stop_reason: str | None, usage: Mapping[str, object],
        actual_credits: int, result: ModelResultDraft,
    ) -> RecoveryOutcome: ...

    async def renew_model_attempt(
        self, *, attempt_id: str, run_execution_token: str,
        attempt_execution_token: str, expected_state_version: int,
        lease_seconds: int = 120,
    ) -> int: ...

    async def claim_action_dispatch(
        self, *, worker_id: str, claim_request_id: str,
        batch_size: int = 10, lease_seconds: int = 120,
    ) -> tuple[ActionDispatchSnapshot, ...]: ...

    async def get_action_dispatch_batch(
        self, *, worker_id: str, claim_request_id: str,
    ) -> tuple[ActionDispatchSnapshot, ...]: ...

    async def claim_action_reconciliation(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> ActionRecoveryClaim: ...

    async def claim_child_cancel(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> ChildCancelRecoveryClaim: ...

    async def apply_child_cancel(
        self, *, intent_id: str, claim_token: str,
        expected_state_version: int, reason: str,
    ) -> RecoveryOutcome: ...
