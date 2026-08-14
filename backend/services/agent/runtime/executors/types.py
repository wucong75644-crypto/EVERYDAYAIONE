"""Immutable Executor descriptor contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.agent.runtime.domain.identity import require_stable_value


class ExecutionMode(StrEnum):
    IMMEDIATE_READ = "immediate_read"
    LOCAL_RENDER = "local_render"
    SANDBOX_JOB = "sandbox_job"
    RESOURCE_MUTATION = "resource_mutation"
    ASYNC_GENERATION = "async_generation"
    EXTERNAL_ACTION = "external_action"
    REMOTE_EXTENSION = "remote_extension"
    CHILD_RUN = "child_run"


class IdempotencySupport(StrEnum):
    NATIVE = "native"
    ADAPTER = "adapter"
    NONE = "none"


class CancellationSupport(StrEnum):
    SUPPORTED = "supported"
    BEST_EFFORT = "best_effort"
    UNSUPPORTED = "unsupported"


class AuthorizationRequirement(StrEnum):
    NONE = "none"
    EXPLICIT_INTENT = "explicit_intent"
    PERSISTED_INTERACTION = "persisted_interaction"
    PREAPPROVED_WORKFLOW = "preapproved_workflow"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, kw_only=True)
class ExecutorDescriptor:
    """Code-level SSOT for one versioned professional Executor."""

    executor_type: str
    revision: int
    action_kinds: frozenset[str]
    mode: ExecutionMode
    authorization: AuthorizationRequirement
    required_capabilities: frozenset[str]
    max_inline_ms: int
    prepare_timeout_ms: int
    submit_timeout_ms: int
    execution_timeout_ms: int
    reconcile_timeout_ms: int
    idempotency: IdempotencySupport
    cancellation: CancellationSupport
    query_status: bool
    progress: bool
    callback: bool
    result_schema_revision: int

    def __post_init__(self) -> None:
        require_stable_value(self.executor_type, "executor_type")
        if self.revision < 1 or self.result_schema_revision < 1:
            raise ValueError("descriptor revisions must be positive")
        if not self.action_kinds:
            raise ValueError("descriptor requires at least one action kind")
        if any(not value.strip() for value in self.action_kinds):
            raise ValueError("action kinds must be stable")
        if any(not value.strip() for value in self.required_capabilities):
            raise ValueError("capability names must be stable")
        timeouts = (
            self.prepare_timeout_ms,
            self.submit_timeout_ms,
            self.execution_timeout_ms,
            self.reconcile_timeout_ms,
        )
        if self.max_inline_ms < 0 or any(value < 1 for value in timeouts):
            raise ValueError("descriptor timeouts must be positive")
        if (
            self.mode is ExecutionMode.IMMEDIATE_READ
            and self.max_inline_ms > 1000
        ):
            raise ValueError("immediate reads must remain bounded")
        if self.query_status and self.mode is ExecutionMode.IMMEDIATE_READ:
            raise ValueError("immediate reads cannot advertise reconciliation")
