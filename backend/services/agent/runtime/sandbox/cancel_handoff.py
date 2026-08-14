"""Sandbox-worker-only cancellation proof and cleanup helpers."""

from __future__ import annotations

import hashlib

from services.agent.runtime.domain.sandbox_job import SandboxCleanupStatus

from .contracts import SandboxResourceLimits
from .receipt import _postgres_jsonb_text, build_receipt


async def finish_active_cancel(worker, job, process, result) -> str:
    accepted = await process.request_cancel()
    if not accepted:
        await worker._mark_unknown(job, "SANDBOX_CANCEL_SIGNAL_UNPROVEN")
        return "unknown"
    signal = await worker._jobs.record_cancel_signal(
        job_id=job.job_id, claim_token=_claim(job),
        fencing_token=job.fencing_token,
        expected_version=job.state_version, signal_state="accepted",
    )
    job = _job(signal)
    if not await process.prove_terminated():
        await worker._mark_unknown(job, "SANDBOX_PROCESS_TREE_UNPROVEN")
        return "unknown"
    confirmed = await worker._jobs.record_cancel_signal(
        job_id=job.job_id, claim_token=_claim(job),
        fencing_token=job.fencing_token,
        expected_version=job.state_version, signal_state="confirmed",
    )
    job = _job(confirmed)
    _, output_dir = worker._workspace.prepare_job(job.job_id)
    limits = SandboxResourceLimits.from_request(job.resource_limits)
    partials = worker._workspace.quarantine(
        job.job_id, output_dir,
        max_bytes=limits.disk_bytes, max_files=limits.file_count,
    )
    digest, receipt = build_receipt(
        execution_outcome="interrupted", stdout=result.stdout,
        stderr=result.stderr, partials=partials, cleaned=True,
    )
    await worker._write_checkpoint(
        job, "cancelled", "PROCESS_TREE_TERMINATED", digest, receipt,
    )
    if partials:
        await worker._mark_unknown(
            job, "SANDBOX_CANCELLED_PENDING_CLEANUP", partials=partials,
        )
        return "unknown"
    if (
        not worker._workspace.cleanup_job(job.job_id)
        or not worker._cleanup_inputs(job)
    ):
        await worker._mark_unknown(job, "SANDBOX_CANCEL_CLEANUP_UNPROVEN")
        return "unknown"
    terminal = await worker._jobs.finish(
        job_id=job.job_id, claim_token=_claim(job),
        fencing_token=job.fencing_token,
        expected_version=job.state_version, terminal_status="cancelled",
        terminal_reason="PROCESS_TREE_TERMINATED",
        receipt_hash=digest, receipt=receipt,
    )
    if terminal.outcome.value in {"cancelled", "already_terminal"}:
        worker._workspace.cleanup_terminal_checkpoint(job.job_id)
    return terminal.outcome.value


async def finish_unstarted_cancel(worker, job) -> str:
    _assert_unstarted(job)
    if not worker._workspace.cleanup_job(job.job_id) or not worker._cleanup_inputs(job):
        await worker._mark_unknown(job, "SANDBOX_CANCEL_CLEANUP_UNPROVEN")
        return "unknown"
    digest, receipt = build_receipt(
        execution_outcome="interrupted", stdout=b"", stderr=b"", cleaned=True,
    )
    await worker._write_checkpoint(
        job, "cancelled", "CANCELLED_BEFORE_START", digest, receipt,
    )
    terminal = await worker._jobs.finish(
        job_id=job.job_id, claim_token=_claim(job),
        fencing_token=job.fencing_token,
        expected_version=job.state_version, terminal_status="cancelled",
        terminal_reason="CANCELLED_BEFORE_START",
        receipt_hash=digest, receipt=receipt,
    )
    if terminal.outcome.value in {"cancelled", "already_terminal"}:
        worker._workspace.cleanup_terminal_checkpoint(job.job_id)
    return terminal.outcome.value


async def reconcile_unstarted_cancel(worker, job) -> str:
    _assert_unstarted(job)
    if not worker._workspace.cleanup_job(job.job_id) or not worker._cleanup_inputs(job):
        await worker._record_cleanup_unknown(job)
        return "cleanup_unknown"
    if job.cleanup_status is not SandboxCleanupStatus.NOT_REQUIRED:
        cleaned = await worker._jobs.record_cleanup(
            job_id=job.job_id,
            reconciliation_token=_reconciliation(job),
            expected_version=job.state_version, cleanup_status="completed",
            cleanup_evidence={"kind": "SANDBOX_CANCEL_CLEANUP_CONFIRMED"},
        )
        job = _job(cleaned)
    digest, receipt = _unstarted_receipt(job.cleanup_status)
    terminal = await worker._jobs.resolve_reconciliation(
        job_id=job.job_id,
        reconciliation_token=_reconciliation(job),
        expected_version=job.state_version, resolution="cancelled",
        terminal_reason="CANCELLED_BEFORE_START",
        receipt_hash=digest, receipt=receipt,
    )
    return terminal.outcome.value


def _unstarted_receipt(cleanup_status: SandboxCleanupStatus):
    digest, receipt = build_receipt(
        execution_outcome="interrupted", stdout=b"", stderr=b"", cleaned=True,
    )
    if cleanup_status is SandboxCleanupStatus.NOT_REQUIRED:
        return digest, receipt
    receipt["cleanup_status"] = "completed"
    receipt["cleanup_evidence"] = {
        "kind": "SANDBOX_CANCEL_CLEANUP_CONFIRMED",
    }
    digest = hashlib.sha256(_postgres_jsonb_text(receipt).encode()).hexdigest()
    return digest, receipt


def _assert_unstarted(job) -> None:
    if (
        job.cancel_confirmed_at is None
        or job.starting_at is not None
        or job.started_at is not None
    ):
        raise RuntimeError("SANDBOX_UNSTARTED_CANCEL_PROOF_REQUIRED")


def _claim(job) -> str:
    if not job.claim_token:
        raise RuntimeError("SANDBOX_CLAIM_TOKEN_REQUIRED")
    return job.claim_token


def _reconciliation(job) -> str:
    if not job.reconciliation_token:
        raise RuntimeError("SANDBOX_RECONCILIATION_TOKEN_REQUIRED")
    return job.reconciliation_token


def _job(receipt):
    if receipt.job is None:
        raise RuntimeError("SANDBOX_JOB_READBACK_REQUIRED")
    return receipt.job
