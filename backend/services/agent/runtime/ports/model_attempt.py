"""ModelAttempt 持久化与结算 Port。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.domain.model_attempt import (
    ModelAttemptStatus,
    ModelDispatchPhase,
    ModelLateOutcome,
    ModelRetryDisposition,
)


class ModelAttemptOutcome(StrEnum):
    PREPARED = "prepared"
    ALREADY_PREPARED = "already_prepared"
    UNRESOLVED_ATTEMPT = "unresolved_attempt"
    INSUFFICIENT_CREDITS = "insufficient_credits"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DISPATCHING = "dispatching"
    ALREADY_DISPATCHING = "already_dispatching"
    RESPONSE_STARTED = "response_started"
    ALREADY_STARTED = "already_started"
    UNKNOWN = "unknown"
    ALREADY_UNKNOWN = "already_unknown"
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    FAILED = "failed"
    ALREADY_FAILED = "already_failed"
    HANDOFF_TOOL_CALLS = "handoff_tool_calls"
    RUN_CANCELLED_USE_LATE_RECEIPT = "run_cancelled_use_late_receipt"
    CLAIMED = "claimed"
    BUSY = "busy"
    NOT_FOUND = "not_found"
    NOT_RECONCILABLE = "not_reconcilable"
    RENEWED = "renewed"
    STILL_UNKNOWN = "still_unknown"
    RECORDED = "recorded"
    ALREADY_RECORDED = "already_recorded"
    RECEIPT_CONFLICT = "receipt_conflict"
    ADJUSTMENT_PENDING = "adjustment_pending"


@dataclass(frozen=True, kw_only=True)
class ModelAttemptReceipt:
    outcome: ModelAttemptOutcome
    attempt_id: str | None = None
    model_step_id: str | None = None
    status: ModelAttemptStatus | None = None
    dispatch_phase: ModelDispatchPhase | None = None
    retry_disposition: ModelRetryDisposition | None = None
    state_version: int | None = None
    attempt_number: int | None = None
    execution_token: str | None = None
    lease_expires_at: datetime | None = None
    event_sequence: int | None = None
    settlement_outcome: str | None = None


@dataclass(frozen=True, kw_only=True)
class ModelAttemptSnapshot:
    attempt_id: str
    model_step_id: str
    run_id: str
    attempt_number: int
    request_hash: str
    idempotency_key: str
    provider: str
    provider_request_id: str | None
    status: ModelAttemptStatus
    dispatch_phase: ModelDispatchPhase
    retry_disposition: ModelRetryDisposition
    response_hash: str | None
    late_outcome: ModelLateOutcome | None
    late_actual_credits: int | None
    late_ambiguity_evidence: Mapping[str, object] | None
    terminal_error_code: str | None
    state_version: int


class ModelAttemptRepositoryPort(Protocol):
    async def prepare(
        self, *, model_step_id: str, run_execution_token: str,
        expected_step_version: int, worker_id: str, request_hash: str,
        idempotency_key: str, provider: str,
        request_receipt: Mapping[str, object], reserved_credits: int,
    ) -> ModelAttemptReceipt: ...

    async def start_dispatch(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, request_hash: str,
    ) -> ModelAttemptReceipt: ...

    async def mark_response_started(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, request_hash: str,
        provider_request_id: str | None,
    ) -> ModelAttemptReceipt: ...

    async def record_unknown(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, request_hash: str,
        dispatch_phase: str, retry_disposition: str,
        ambiguity_evidence: Mapping[str, object],
    ) -> ModelAttemptReceipt: ...

    async def complete_without_actions(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, response_receipt: Mapping[str, object],
        response_hash: str, stop_reason: str,
        provider_stop_reason: str | None, usage: Mapping[str, object],
        actual_credits: int,
    ) -> ModelAttemptReceipt: ...

    async def fail(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, error_code: str,
        retry_disposition: str = "forbidden",
    ) -> ModelAttemptReceipt: ...

    async def get_attempt(
        self, attempt_id: str,
    ) -> ModelAttemptSnapshot | None: ...

    async def claim_reconciliation(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, worker_id: str,
        lease_seconds: int = 120,
    ) -> ModelAttemptReceipt: ...

    async def renew_reconciliation(
        self, *, attempt_id: str, run_execution_token: str,
        reconciliation_token: str, lease_seconds: int = 120,
    ) -> ModelAttemptReceipt: ...

    async def resolve(
        self, *, attempt_id: str, run_execution_token: str,
        reconciliation_token: str, expected_attempt_version: int,
        expected_step_version: int, resolution: str, request_hash: str,
        response_receipt: Mapping[str, object] | None = None,
        response_hash: str | None = None, stop_reason: str | None = None,
        provider_stop_reason: str | None = None,
        usage: Mapping[str, object] | None = None, actual_credits: int = 0,
        error_code: str | None = None,
        ambiguity_evidence: Mapping[str, object] | None = None,
    ) -> ModelAttemptReceipt: ...

    async def record_late_receipt(
        self, *, attempt_id: str, provider_request_id: str | None,
        response_receipt: Mapping[str, object], response_hash: str,
        usage: Mapping[str, object], late_outcome: str,
        ambiguity_evidence: Mapping[str, object], actual_credits: int,
    ) -> ModelAttemptReceipt: ...
