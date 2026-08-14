"""Trusted issuer for attempt-scoped Sandbox capabilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.agent.runtime.domain import ActionAttempt, ActionAttemptStatus
from services.agent.runtime.executors.capabilities import CapabilityBinding
from services.agent.runtime.executors.sandbox_job import (
    SANDBOX_JOB_DESCRIPTOR,
)
from services.agent.runtime.infrastructure.postgres.sandbox_job_repository import (
    PostgresSandboxJobRepository,
)

from .capability import SandboxJobCapability
from .workspace import SandboxWorkspaceStore


class SandboxCapabilityIssuer:
    def __init__(
        self, *, jobs: PostgresSandboxJobRepository,
        workspace: SandboxWorkspaceStore, runtime_revision: str,
        ttl_seconds: int = 300,
    ) -> None:
        if ttl_seconds < 15 or ttl_seconds > 600:
            raise ValueError("SANDBOX_CAPABILITY_TTL_INVALID")
        self._jobs = jobs
        self._workspace = workspace
        self._runtime_revision = runtime_revision
        self._ttl_seconds = ttl_seconds

    def issue(
        self, *, attempt: ActionAttempt, descriptor: object,
        phase: str, dispatch_gate: object | None = None,
    ) -> dict[str, object]:
        if descriptor != SANDBOX_JOB_DESCRIPTOR:
            return {}
        if phase == "dispatch":
            if attempt.status is not ActionAttemptStatus.DISPATCHING:
                raise PermissionError("SANDBOX_DISPATCH_ATTEMPT_REQUIRED")
            if (
                dispatch_gate is None
                or not getattr(dispatch_gate, "intent_id", None)
                or not getattr(
                    dispatch_gate, "external_idempotency_key", None,
                )
            ):
                raise PermissionError("SANDBOX_DISPATCH_GATE_REQUIRED")
            operations = frozenset({"submit", "cleanup"})
        elif phase == "reconcile":
            if attempt.status not in {
                ActionAttemptStatus.ACCEPTED,
                ActionAttemptStatus.UNKNOWN,
            }:
                raise PermissionError("SANDBOX_RECONCILE_ATTEMPT_REQUIRED")
            operations = frozenset({"get", "readback", "cleanup"})
        elif phase == "cancel":
            if attempt.status not in {
                ActionAttemptStatus.ACCEPTED,
                ActionAttemptStatus.UNKNOWN,
            }:
                raise PermissionError("SANDBOX_CANCEL_ATTEMPT_REQUIRED")
            operations = frozenset({"get", "cancel"})
        else:
            raise PermissionError("SANDBOX_CAPABILITY_PHASE_INVALID")
        binding = CapabilityBinding(
            action_id=str(attempt.action_id),
            attempt_id=str(attempt.attempt_id),
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=self._ttl_seconds),
            obligations=frozenset({
                "dispatch_intent_bound",
                "reconcile_only_after_submit",
            }),
        )
        return {"sandbox_job": SandboxJobCapability(
            binding=binding, _jobs=self._jobs, _workspace=self._workspace,
            runtime_revision=self._runtime_revision,
            allowed_operations=operations,
        )}
