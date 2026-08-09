"""Typed persistence contract for Action lifecycle RPCs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol, Sequence


class ActionMutationOutcome(StrEnum):
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    FAILED = "failed"
    ALREADY_FAILED = "already_failed"
    CLAIMED = "claimed"
    FOUND = "found"
    NOT_FOUND = "not_found"
    DISPATCHING = "dispatching"
    ALREADY_DISPATCHING = "already_dispatching"
    ACCEPTED = "accepted"
    ALREADY_ACCEPTED = "already_accepted"
    UNKNOWN = "unknown"
    ALREADY_UNKNOWN = "already_unknown"
    RENEWED = "renewed"
    BUSY = "busy"
    NOT_RECONCILABLE = "not_reconcilable"
    STILL_UNKNOWN = "still_unknown"
    CANCELLED = "cancelled"
    ALREADY_CANCELLED = "already_cancelled"
    RUN_CANCELLED = "run_cancelled"


@dataclass(frozen=True, kw_only=True)
class ActionMutationReceipt:
    outcome: ActionMutationOutcome
    action_id: str | None = None
    attempt_id: str | None = None
    model_step_id: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    state_version: int | None = None
    blocking_action_count: int | None = None
    batch_hash: str | None = None
    result_hash: str | None = None
    execution_token: str | None = None
    lease_expires_at: datetime | None = None
    action_ids: tuple[str, ...] = ()
    attempts: tuple[Mapping[str, object], ...] = ()
    action: Mapping[str, object] | None = None
    attempt: Mapping[str, object] | None = None
    result: Mapping[str, object] | None = None


class ActionRepositoryPort(Protocol):
    async def complete_tool_calls(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, response_receipt: Mapping[str, object],
        response_hash: str, provider_stop_reason: str | None,
        usage: Mapping[str, object], actual_credits: int,
        batch_hash: str, actions: Sequence[Mapping[str, object]],
    ) -> ActionMutationReceipt: ...

    async def claim_ready(
        self, *, worker_id: str, claim_request_id: str,
        batch_size: int = 10,
        lease_seconds: int = 120,
    ) -> ActionMutationReceipt: ...

    async def get_claim_batch(
        self, *, worker_id: str, claim_request_id: str,
    ) -> ActionMutationReceipt: ...

    async def get_action(
        self, *, action_id: str,
    ) -> ActionMutationReceipt: ...

    async def renew(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, lease_seconds: int = 120,
    ) -> ActionMutationReceipt: ...

    async def mark_dispatching(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, request_hash: str,
    ) -> ActionMutationReceipt: ...

    async def recover_expired(
        self, *, attempt_id: str, expected_state_version: int,
        worker_id: str, lease_seconds: int = 120,
    ) -> ActionMutationReceipt: ...

    async def mark_accepted(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, request_hash: str,
        external_receipt: Mapping[str, object],
    ) -> ActionMutationReceipt: ...

    async def record_unknown(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, request_hash: str,
        ambiguity_evidence: Mapping[str, object],
    ) -> ActionMutationReceipt: ...

    async def fail_before_dispatch(
        self, *, attempt_id: str, execution_token: str,
        expected_attempt_version: int, request_hash: str, error_code: str,
    ) -> ActionMutationReceipt: ...

    async def complete(
        self, *, attempt_id: str, execution_token: str,
        expected_attempt_version: int, request_hash: str,
        result: Mapping[str, object],
    ) -> ActionMutationReceipt: ...

    async def fail(
        self, *, attempt_id: str, execution_token: str,
        expected_attempt_version: int, request_hash: str,
        result: Mapping[str, object],
    ) -> ActionMutationReceipt: ...

    async def claim_reconciliation(
        self, *, attempt_id: str, expected_state_version: int,
        worker_id: str, lease_seconds: int = 120,
    ) -> ActionMutationReceipt: ...

    async def renew_reconciliation(
        self, *, attempt_id: str, reconciliation_token: str,
        expected_state_version: int, lease_seconds: int = 120,
    ) -> ActionMutationReceipt: ...

    async def resolve_reconciliation(
        self, *, attempt_id: str, reconciliation_token: str,
        expected_state_version: int, request_hash: str, resolution: str,
        result: Mapping[str, object] | None = None,
        ambiguity_evidence: Mapping[str, object] | None = None,
    ) -> ActionMutationReceipt: ...

    async def finalize_sandbox_cancel(
        self, *, attempt_id: str, reconciliation_token: str,
        expected_state_version: int, request_hash: str,
        sandbox_job_id: str, expected_job_state_version: int,
        receipt_hash: str,
    ) -> ActionMutationReceipt: ...

    async def finalize_child_cancel(
        self, *, attempt_id: str, reconciliation_token: str,
        expected_state_version: int, request_hash: str,
        intent_id: str, proof_hash: str,
    ) -> ActionMutationReceipt: ...
