"""Persistent Sandbox Job state and recovery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from services.agent.runtime.domain.execution import require_aware_datetime
from services.agent.runtime.domain.identity import require_stable_value


class SandboxJobStatus(StrEnum):
    PREPARED = "prepared"
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTING = "starting"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class SandboxCleanupStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class SandboxMaterializationStatus(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class SandboxJobSnapshot:
    job_id: str
    action_id: str
    attempt_id: str
    dispatch_intent_id: str
    external_idempotency_key: str
    request_hash: str
    code_sha256: str
    status: SandboxJobStatus
    state_version: int
    fencing_token: int
    cleanup_status: SandboxCleanupStatus
    materialization_status: SandboxMaterializationStatus
    queued_at: datetime
    claim_token: str | None = None
    lease_expires_at: datetime | None = None
    reconciliation_token: str | None = None
    reconciliation_lease_expires_at: datetime | None = None
    terminal_at: datetime | None = None
    artifact_manifest: Mapping[str, object] | None = None
    partial_effects: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.job_id, "job_id"),
            (self.action_id, "action_id"),
            (self.attempt_id, "attempt_id"),
            (self.dispatch_intent_id, "dispatch_intent_id"),
            (self.external_idempotency_key, "external_idempotency_key"),
        ):
            require_stable_value(value, name)
        for value, name in (
            (self.request_hash, "request_hash"),
            (self.code_sha256, "code_sha256"),
        ):
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256")
        if self.state_version < 0 or self.fencing_token < 0:
            raise ValueError("Sandbox Job versions must be nonnegative")
        require_aware_datetime(self.queued_at, "queued_at")
        require_aware_datetime(self.lease_expires_at, "lease_expires_at")
        require_aware_datetime(
            self.reconciliation_lease_expires_at,
            "reconciliation_lease_expires_at",
        )
        require_aware_datetime(self.terminal_at, "terminal_at")
        terminal = {
            SandboxJobStatus.SUCCEEDED,
            SandboxJobStatus.FAILED,
            SandboxJobStatus.TIMED_OUT,
            SandboxJobStatus.CANCELLED,
        }
        if (self.status in terminal) != (self.terminal_at is not None):
            raise ValueError("terminal Sandbox Job requires terminal_at")
        if self.status is SandboxJobStatus.UNKNOWN and not self.partial_effects:
            # Unknown without partial effects remains valid; ambiguity is retained
            # by PostgreSQL and intentionally omitted from this narrow snapshot.
            return
