"""Explicit Runtime-owned cancellation paths for recoverable Actions."""

from __future__ import annotations

from dataclasses import replace

from services.agent.runtime.application.action_loop_support import (
    ActionLease,
    next_reconcile_at,
    required,
    required_int,
    required_time,
    reserved_amount,
)
from services.agent.runtime.domain import ActionAttemptStatus
from services.agent.runtime.executors.sandbox_job import SandboxJobExecutor
from services.agent.runtime.executors.specialist_contracts import (
    ReconciliationContext,
)
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.ports.coordinator_recovery import ActionDispatchSnapshot
from services.agent.runtime.ports.executor import ExecutionOutcome


async def cancel_action(
    driver, snapshot: ActionDispatchSnapshot, *, lease: ActionLease | None,
) -> ExecutionOutcome:
    """Run only explicitly supported cancel owners under an Action lease."""
    resolved = driver._resolver.resolve(snapshot)
    if not isinstance(resolved.executor, (SpecialistExecutor, SandboxJobExecutor)):
        raise TypeError("ACTION_CANCEL_OWNER_NOT_SUPPORTED")
    raw_attempt = snapshot.attempt
    status = str(raw_attempt.get("status", ""))
    reconciliation = status in {"accepted", "unknown"}
    if not reconciliation:
        raise TypeError("ACTION_CANCEL_RECONCILIATION_REQUIRED")
    token = required(raw_attempt.get("reconciliation_token"), "reconciliation_token")
    state_version = required_int(raw_attempt.get("state_version"), "cancel state version")
    active_lease = lease or ActionLease(
        repository=driver._actions, attempt_id=str(raw_attempt["id"]),
        token=token, state_version=state_version,
        lease_seconds=driver._lease_seconds,
        renew_interval=driver._renew_interval, reconciliation=True,
    )
    attempt = replace(
        resolved.attempt, status=ActionAttemptStatus(status),
    )
    attempt = driver._with_capabilities(
        attempt, resolved.descriptor, "cancel",
    )
    context = ReconciliationContext(
        token=token,
        lease_expires_at=required_time(
            raw_attempt.get("reconciliation_lease_expires_at"),
        ),
        state_version=state_version,
    )
    receipt = await active_lease.run(resolved.executor.cancel(attempt, context))
    request_hash = str(raw_attempt["request_hash"])
    if receipt.request_hash != request_hash:
        raise RuntimeError("EXECUTOR_REQUEST_HASH_CONFLICT")
    if isinstance(resolved.executor, SpecialistExecutor):
        await _settle_specialist(
            driver, snapshot, receipt, token, active_lease.state_version,
            child_run=resolved.descriptor.executor_type.startswith(
                "runtime_child_run:",
            ),
        )
    else:
        await _settle_sandbox(
            driver, snapshot, receipt, token, active_lease.state_version,
        )
    return receipt.outcome


async def _settle_specialist(
    driver, snapshot, receipt, token, state_version, *, child_run: bool,
) -> None:
    raw_attempt = snapshot.attempt
    if receipt.outcome is ExecutionOutcome.CANCELLED:
        if child_run:
            await _settle_child_cancel(
                driver, raw_attempt, receipt, token, state_version,
            )
            return
        finalized = await driver._try_specialist_finalize(
            receipt, attempt_id=str(raw_attempt["id"]), token=token,
            state_version=state_version,
            request_hash=str(raw_attempt["request_hash"]),
            reconciliation=True, reserved_amount=reserved_amount(snapshot),
            specialist=True,
        )
        if not finalized:
            raise RuntimeError("SPECIALIST_CANCEL_FINALIZE_REQUIRED")
        return
    if receipt.outcome is not ExecutionOutcome.UNKNOWN:
        raise RuntimeError("SPECIALIST_CANCEL_UNEXPECTED_OUTCOME")
    await driver._specialist_facts.still_unknown(
        attempt_id=str(raw_attempt["id"]), reconciliation_token=token,
        expected_state_version=state_version,
        request_hash=str(raw_attempt["request_hash"]),
        provider_receipt=dict(receipt.external_receipt),
        ambiguity_evidence=receipt.ambiguity_evidence,
        next_reconcile_at=next_reconcile_at(driver._lease_seconds),
    )


async def _settle_child_cancel(
    driver, raw_attempt, receipt, token, state_version,
) -> None:
    evidence = receipt.external_receipt.get("evidence")
    if not isinstance(evidence, dict):
        raise RuntimeError("CHILD_CANCEL_PROOF_REQUIRED")
    await driver._actions.finalize_child_cancel(
        attempt_id=str(raw_attempt["id"]), reconciliation_token=token,
        expected_state_version=state_version,
        request_hash=str(raw_attempt["request_hash"]),
        intent_id=required(
            evidence.get("child_cancel_intent_id"),
            "child_cancel_intent_id",
        ),
        proof_hash=required(evidence.get("proof_hash"), "proof_hash"),
    )


async def _settle_sandbox(driver, snapshot, receipt, token, state_version) -> None:
    raw_attempt = snapshot.attempt
    request_hash = str(raw_attempt["request_hash"])
    if receipt.outcome is ExecutionOutcome.CANCELLED:
        proof = receipt.external_receipt
        await driver._actions.finalize_sandbox_cancel(
            attempt_id=str(raw_attempt["id"]), reconciliation_token=token,
            expected_state_version=state_version, request_hash=request_hash,
            sandbox_job_id=required(proof.get("sandbox_job_id"), "sandbox_job_id"),
            expected_job_state_version=required_int(
                proof.get("state_version"), "sandbox_job_state_version",
            ),
            receipt_hash=required(proof.get("receipt_hash"), "receipt_hash"),
        )
        return
    if receipt.outcome is not ExecutionOutcome.UNKNOWN:
        raise RuntimeError("SANDBOX_CANCEL_UNEXPECTED_OUTCOME")
    await driver._actions.resolve_reconciliation(
        attempt_id=str(raw_attempt["id"]), reconciliation_token=token,
        expected_state_version=state_version, request_hash=request_hash,
        resolution="still_unknown",
        ambiguity_evidence=(
            receipt.ambiguity_evidence
            or {"kind": "SANDBOX_CANCEL_UNPROVEN"}
        ),
    )
