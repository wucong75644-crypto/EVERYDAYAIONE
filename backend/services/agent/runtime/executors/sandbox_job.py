"""Professional code_execute Executor backed by persistent Sandbox Jobs."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from services.agent.runtime.domain import (
    ActionAttempt,
    ActionResult,
    ActionResultStatus,
)
from services.agent.runtime.domain.sandbox_job import SandboxJobStatus
from services.agent.runtime.domain.errors import (
    IdempotencyConflictError,
    PersistenceContractError,
)
from services.agent.runtime.executors.types import (
    AuthorizationRequirement,
    CancellationSupport,
    ExecutionMode,
    ExecutorDescriptor,
    IdempotencySupport,
)
from services.agent.runtime.ports.executor import (
    ExecutionOutcome,
    ExecutionReceipt,
    ExecutorDispatchUnknown,
)
from services.agent.runtime.ports.sandbox_job import SandboxJobOutcome
from services.agent.runtime.sandbox.capability import SandboxJobCapability
from services.agent.runtime.sandbox.contracts import SandboxResourceLimits


SANDBOX_EXECUTOR_TYPE = "sandbox_job"
SANDBOX_EXECUTOR_REVISION = 1

SANDBOX_JOB_DESCRIPTOR = ExecutorDescriptor(
    executor_type=SANDBOX_EXECUTOR_TYPE,
    revision=SANDBOX_EXECUTOR_REVISION,
    action_kinds=frozenset({"code_execute"}),
    mode=ExecutionMode.SANDBOX_JOB,
    authorization=AuthorizationRequirement.PERSISTED_INTERACTION,
    required_capabilities=frozenset({"sandbox_job"}),
    max_inline_ms=0,
    prepare_timeout_ms=5_000,
    submit_timeout_ms=10_000,
    execution_timeout_ms=300_000,
    reconcile_timeout_ms=10_000,
    idempotency=IdempotencySupport.NATIVE,
    cancellation=CancellationSupport.BEST_EFFORT,
    query_status=True,
    progress=False,
    callback=False,
    result_schema_revision=1,
)


class SandboxJobExecutor:
    async def dispatch(
        self, attempt: ActionAttempt, request: Mapping[str, object],
    ) -> ExecutionReceipt:
        context = _mapping(request.get("_dispatch_context"), "dispatch_context")
        code = request.get("code")
        if not isinstance(code, str) or not code:
            return _failed(attempt, "SANDBOX_CODE_REQUIRED")
        limits = SandboxResourceLimits.from_request(
            _optional_mapping(request.get("resource_limits")),
        )
        capability = _capability(attempt)
        binding = _recovery_binding(attempt, request, context, capability)
        try:
            receipt = await capability.submit(
                action_id=str(attempt.action_id),
                attempt_id=str(attempt.attempt_id),
                dispatch_intent_id=_text(context, "dispatch_intent_id"),
                expected_action_version=_integer(
                    context, "expected_action_version",
                ),
                expected_attempt_version=_integer(
                    context, "expected_attempt_version",
                ),
                external_idempotency_key=_text(
                    request, "external_idempotency_key",
                ),
                request_hash=attempt.request_hash,
                executor_type=SANDBOX_EXECUTOR_TYPE,
                executor_revision=SANDBOX_EXECUTOR_REVISION,
                workspace_scope_ref=_workspace_scope(attempt),
                code=code,
                input_manifest=_input_manifest(request),
                resource_limits=limits,
            )
        except IdempotencyConflictError:
            _cleanup_staged(capability, attempt)
            return _failed(attempt, "SANDBOX_SUBMIT_IDEMPOTENCY_CONFLICT")
        except PersistenceContractError:
            _cleanup_staged(capability, attempt)
            return _failed(attempt, "SANDBOX_SUBMIT_CONTRACT_REJECTED")
        except Exception as error:
            raise ExecutorDispatchUnknown(binding) from error
        if receipt.job is None:
            _cleanup_staged(capability, attempt)
            return _failed(
                attempt,
                f"SANDBOX_SUBMIT_{receipt.outcome.value.upper()}",
            )
        if receipt.outcome.value not in {"created", "already_created"}:
            return _failed(
                attempt, f"SANDBOX_SUBMIT_{receipt.outcome.value.upper()}",
            )
        return ExecutionReceipt(
            outcome=ExecutionOutcome.ACCEPTED,
            request_hash=attempt.request_hash,
            external_receipt={
                "sandbox_job_id": receipt.job.job_id,
                "external_idempotency_key": receipt.job.external_idempotency_key,
                "status": receipt.job.status.value,
            },
        )

    async def reconcile(self, attempt: ActionAttempt) -> ExecutionReceipt:
        capability = _capability(attempt)
        job_id = _optional_job_id(attempt)
        try:
            if job_id is None:
                receipt = await capability.readback_after_submit_loss(
                    action_id=str(attempt.action_id),
                    attempt_id=str(attempt.attempt_id),
                    request_hash=attempt.request_hash,
                    binding=attempt.ambiguity_evidence,
                )
            else:
                receipt = await capability.get(
                    action_id=str(attempt.action_id),
                    attempt_id=str(attempt.attempt_id), job_id=job_id,
                )
        except IdempotencyConflictError:
            return _failed(attempt, "SANDBOX_READBACK_IDEMPOTENCY_CONFLICT")
        except Exception:
            return _unknown(attempt, "SANDBOX_READBACK_UNAVAILABLE")
        if receipt.job is None:
            if receipt.outcome is SandboxJobOutcome.NOT_FOUND:
                _cleanup_staged(capability, attempt)
                return _failed(attempt, "SANDBOX_SUBMIT_NOT_FOUND")
            return _unknown(attempt, "SANDBOX_JOB_READBACK_MISSING")
        job = receipt.job
        if job.status is SandboxJobStatus.SUCCEEDED:
            return _completed(attempt, job)
        if job.status in {
            SandboxJobStatus.FAILED,
            SandboxJobStatus.TIMED_OUT,
            SandboxJobStatus.CANCELLED,
        }:
            return _failed(
                attempt, job.terminal_reason or "SANDBOX_JOB_FAILED",
            )
        if job.status is SandboxJobStatus.UNKNOWN:
            return _unknown(attempt, "SANDBOX_JOB_STILL_UNKNOWN")
        return ExecutionReceipt(
            outcome=ExecutionOutcome.ACCEPTED,
            request_hash=attempt.request_hash,
            external_receipt={
                "sandbox_job_id": job.job_id, "status": job.status.value,
            },
        )

    async def cancel(self, attempt: ActionAttempt) -> ExecutionReceipt:
        job_id = _job_id(attempt)
        capability = _capability(attempt)
        try:
            current = await capability.get(
                action_id=str(attempt.action_id),
                attempt_id=str(attempt.attempt_id), job_id=job_id,
            )
        except Exception:
            return _unknown(attempt, "SANDBOX_CANCEL_READBACK_UNAVAILABLE")
        if current.job is None:
            return _unknown(attempt, "SANDBOX_JOB_READBACK_MISSING")
        try:
            requested = await capability.request_cancel(
                action_id=str(attempt.action_id),
                attempt_id=str(attempt.attempt_id), job_id=job_id,
                expected_version=current.job.state_version,
            )
        except Exception:
            return _unknown(attempt, "SANDBOX_CANCEL_REQUEST_UNAVAILABLE")
        return ExecutionReceipt(
            outcome=ExecutionOutcome.ACCEPTED,
            request_hash=attempt.request_hash,
            external_receipt={
                "sandbox_job_id": job_id,
                "status": (
                    requested.job.status.value
                    if requested.job else "cancel_requested"
                ),
            },
        )

def register_sandbox_job_executor(registry, executor: SandboxJobExecutor) -> None:
    registry.register(SANDBOX_JOB_DESCRIPTOR, executor)


def _capability(attempt: ActionAttempt) -> SandboxJobCapability:
    value = attempt.capabilities.get("sandbox_job")
    if not isinstance(value, SandboxJobCapability):
        raise PermissionError("SANDBOX_JOB_CAPABILITY_REQUIRED")
    return value


def _completed(attempt: ActionAttempt, job) -> ExecutionReceipt:
    data = {
        "sandbox_job_id": job.job_id,
        "artifact_manifest": dict(job.artifact_manifest or {}),
    }
    canonical = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return ExecutionReceipt(
        outcome=ExecutionOutcome.COMPLETED,
        request_hash=attempt.request_hash,
        result=ActionResult(
            action_id=attempt.action_id, scope=attempt.scope,
            status=ActionResultStatus.SUCCESS,
            result_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            summary=job.stdout_summary or "", data=data,
            receipt={"sandbox_job_id": job.job_id},
        ),
    )


def _failed(attempt: ActionAttempt, code: str) -> ExecutionReceipt:
    return ExecutionReceipt(
        outcome=ExecutionOutcome.FAILED,
        request_hash=attempt.request_hash,
        external_receipt={"error_code": code, "summary": ""},
    )


def _unknown(attempt: ActionAttempt, kind: str) -> ExecutionReceipt:
    return ExecutionReceipt(
        outcome=ExecutionOutcome.UNKNOWN,
        request_hash=attempt.request_hash,
        ambiguity_evidence={"kind": kind},
    )


def _job_id(attempt: ActionAttempt) -> str:
    value = _optional_job_id(attempt)
    if value is None:
        raise ValueError("SANDBOX_JOB_ID_MISSING")
    return value


def _optional_job_id(attempt: ActionAttempt) -> str | None:
    value = attempt.external_receipt.get("sandbox_job_id")
    return value if isinstance(value, str) and value else None


def _workspace_scope(attempt: ActionAttempt) -> str:
    return f"ws-scope:{attempt.scope.kind.value}:{attempt.scope.scope_id}"


def _input_manifest(request: Mapping[str, object]) -> Mapping[str, object]:
    value = request.get("input_manifest")
    return (
        _mapping(value, "input_manifest")
        if value is not None else {"schema_revision": 1, "items": []}
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"SANDBOX_{name.upper()}_INVALID")
    return value


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    return None if value is None else _mapping(value, "resource_limits")


def _text(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"SANDBOX_{field.upper()}_REQUIRED")
    return item


def _integer(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError(f"SANDBOX_{field.upper()}_REQUIRED")
    return item


def _recovery_binding(
    attempt: ActionAttempt, request: Mapping[str, object],
    context: Mapping[str, object], capability: SandboxJobCapability,
) -> dict[str, object]:
    if attempt.session_id is None or attempt.run_id is None:
        raise ValueError("SANDBOX_RUNTIME_SCOPE_IDENTITY_REQUIRED")
    return {
        "kind": "SANDBOX_SUBMIT_RESULT_UNKNOWN",
        "external_idempotency_key": _text(
            request, "external_idempotency_key",
        ),
        "action_id": str(attempt.action_id),
        "attempt_id": str(attempt.attempt_id),
        "dispatch_intent_id": _text(context, "dispatch_intent_id"),
        "request_hash": attempt.request_hash,
        "org_id": attempt.scope.org_id,
        "user_id": attempt.scope.user_id,
        "session_id": attempt.session_id,
        "run_id": attempt.run_id,
        "executor_type": SANDBOX_EXECUTOR_TYPE,
        "executor_revision": SANDBOX_EXECUTOR_REVISION,
        "runtime_revision": capability.runtime_revision,
        "code_sha256": hashlib.sha256(
            _text(request, "code").encode("utf-8"),
        ).hexdigest(),
    }


def _cleanup_staged(
    capability: SandboxJobCapability, attempt: ActionAttempt,
) -> None:
    if not capability.cleanup_staged_attempt(
        action_id=str(attempt.action_id),
        attempt_id=str(attempt.attempt_id),
    ):
        raise RuntimeError("SANDBOX_STAGED_INPUT_CLEANUP_UNPROVEN")
