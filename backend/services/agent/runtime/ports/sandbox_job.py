"""Typed persistence port for Sandbox Job Controller Batch A."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.domain.sandbox_job import SandboxJobSnapshot


class SandboxJobOutcome(StrEnum):
    CREATED = "created"
    ALREADY_CREATED = "already_created"
    FOUND = "found"
    CLAIMED = "claimed"
    RENEWED = "renewed"
    STARTING = "starting"
    RUNNING = "running"
    REQUEUED = "requeued"
    CANCEL_REQUESTED = "cancel_requested"
    ALREADY_CANCEL_REQUESTED = "already_cancel_requested"
    CANCEL_ACCEPTED = "cancel_accepted"
    CANCEL_CONFIRMED = "cancel_confirmed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    ALREADY_UNKNOWN = "already_unknown"
    ALREADY_TERMINAL = "already_terminal"
    NOT_FOUND = "not_found"
    NOT_RECONCILABLE = "not_reconcilable"
    BUSY = "busy"
    STILL_UNKNOWN = "still_unknown"
    CLEANUP_RUNNING = "cleanup_running"
    CLEANUP_COMPLETED = "cleanup_completed"
    CLEANUP_FAILED = "cleanup_failed"
    CLEANUP_UNKNOWN = "cleanup_unknown"
    PARTIALS_RECORDED = "partials_recorded"
    ALREADY_PARTIALS_RECORDED = "already_partials_recorded"
    PARTIAL_EFFECTS_CONFLICT = "partial_effects_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    DISPATCH_INTENT_INVALID = "dispatch_intent_invalid"
    SCOPE_BINDING_INVALID = "scope_binding_invalid"
    OWNERSHIP_LOST = "ownership_lost"
    STALE_VERSION = "stale_version"
    INVALID_TRANSITION = "invalid_transition"
    MALFORMED_RECEIPT = "malformed_receipt"
    RECEIPT_HASH_CONFLICT = "receipt_hash_conflict"
    TERMINAL_CONFLICT = "terminal_conflict"
    TERMINAL_GUARD_FAILED = "terminal_guard_failed"


@dataclass(frozen=True, kw_only=True)
class SandboxJobReceipt:
    outcome: SandboxJobOutcome
    job: SandboxJobSnapshot | None = None


class SandboxJobRepositoryPort(Protocol):
    async def create_or_get(
        self, *, action_id: str, attempt_id: str, dispatch_intent_id: str,
        expected_action_version: int, expected_attempt_version: int,
        external_idempotency_key: str, request_hash: str,
        executor_type: str, executor_revision: int, runtime_revision: str,
        workspace_scope_ref: str, code_sha256: str,
        input_manifest: Mapping[str, object],
        resource_limits: Mapping[str, object],
    ) -> SandboxJobReceipt: ...

    async def get(self, *, job_id: str) -> SandboxJobReceipt: ...

    async def get_owned(
        self, *, job_id: str, worker_id: str,
        claim_token: str, fencing_token: int,
    ) -> SandboxJobReceipt: ...

    async def readback_by_binding(
        self, *, external_idempotency_key: str, action_id: str,
        attempt_id: str, dispatch_intent_id: str, request_hash: str,
        org_id: str | None, user_id: str | None,
        session_id: str, run_id: str, executor_type: str,
        executor_revision: int, runtime_revision: str,
    ) -> SandboxJobReceipt: ...

    async def claim(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> SandboxJobReceipt: ...

    async def claim_recoverable(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> SandboxJobReceipt: ...

    async def claim_cancel(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> SandboxJobReceipt: ...

    async def claim_next_reconciliation(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> SandboxJobReceipt: ...

    async def renew(
        self, *, job_id: str, claim_token: str, fencing_token: int,
        expected_version: int, lease_seconds: int = 60,
    ) -> SandboxJobReceipt: ...

    async def mark_started(
        self, *, job_id: str, claim_token: str, fencing_token: int,
        expected_version: int, phase: str,
    ) -> SandboxJobReceipt: ...

    async def recover_expired(
        self, *, job_id: str, expected_version: int,
    ) -> SandboxJobReceipt: ...

    async def request_cancel(
        self, *, job_id: str, expected_version: int,
    ) -> SandboxJobReceipt: ...

    async def request_runtime_cancel(
        self, *, job_id: str, attempt_id: str,
        reconciliation_token: str, expected_action_state_version: int,
        request_hash: str,
    ) -> SandboxJobReceipt: ...

    async def record_cancel_signal(
        self, *, job_id: str, claim_token: str, fencing_token: int,
        expected_version: int, signal_state: str,
    ) -> SandboxJobReceipt: ...

    async def finish(
        self, *, job_id: str, claim_token: str, fencing_token: int,
        expected_version: int, terminal_status: str, terminal_reason: str,
        receipt_hash: str, receipt: Mapping[str, object],
    ) -> SandboxJobReceipt: ...

    async def record_unknown(
        self, *, job_id: str, claim_token: str, fencing_token: int,
        expected_version: int,
        ambiguity_evidence: Mapping[str, object],
        partial_effects: Mapping[str, object],
        cleanup_deadline_at: str | None = None,
    ) -> SandboxJobReceipt: ...

    async def claim_reconciliation(
        self, *, job_id: str, expected_version: int, worker_id: str,
        lease_seconds: int = 60,
    ) -> SandboxJobReceipt: ...

    async def renew_reconciliation(
        self, *, job_id: str, reconciliation_token: str,
        expected_version: int, lease_seconds: int = 60,
    ) -> SandboxJobReceipt: ...

    async def resolve_reconciliation(
        self, *, job_id: str, reconciliation_token: str,
        expected_version: int, resolution: str, terminal_reason: str,
        receipt_hash: str, receipt: Mapping[str, object],
    ) -> SandboxJobReceipt: ...

    async def record_cleanup(
        self, *, job_id: str, reconciliation_token: str,
        expected_version: int, cleanup_status: str,
        cleanup_evidence: Mapping[str, object],
    ) -> SandboxJobReceipt: ...

    async def record_reconciled_partials(
        self, *, job_id: str, reconciliation_token: str,
        expected_version: int, partial_effects: Mapping[str, object],
    ) -> SandboxJobReceipt: ...
