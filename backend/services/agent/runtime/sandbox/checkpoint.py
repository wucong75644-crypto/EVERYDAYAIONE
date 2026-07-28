"""Durable terminal-checkpoint reconciliation."""

from __future__ import annotations


async def resolve_terminal_checkpoint(
    *, jobs, workspace, job, checkpoint: dict[str, object],
) -> str:
    receipt = checkpoint.get("receipt")
    digest = checkpoint.get("receipt_hash")
    resolution = checkpoint.get("resolution")
    reason = checkpoint.get("terminal_reason")
    if (
        checkpoint.get("sandbox_job_id") != job.job_id
        or not isinstance(receipt, dict)
        or not isinstance(digest, str)
        or resolution not in {"succeeded", "failed", "timed_out", "cancelled"}
        or not isinstance(reason, str)
        or not job.reconciliation_token
    ):
        raise RuntimeError("SANDBOX_CHECKPOINT_INVALID")
    receipt_partials = receipt.get("partial_effects")
    if not isinstance(receipt_partials, dict):
        raise RuntimeError("SANDBOX_CHECKPOINT_INVALID")
    receipt_items = receipt_partials.get("items")
    if not isinstance(receipt_items, list):
        raise RuntimeError("SANDBOX_CHECKPOINT_INVALID")
    job_partials = job.partial_effects or {
        "schema_revision": 1, "items": [],
    }
    job_items = job_partials.get("items")
    if receipt_items and not job_items:
        recorded = await jobs.record_reconciled_partials(
            job_id=job.job_id,
            reconciliation_token=job.reconciliation_token,
            expected_version=job.state_version,
            partial_effects=receipt_partials,
        )
        if recorded.job is None:
            raise RuntimeError("SANDBOX_PARTIAL_FACT_REQUIRED")
        job = recorded.job
        job_partials = job.partial_effects or {}
    if job_partials != receipt_partials:
        raise RuntimeError("SANDBOX_PARTIAL_FACT_CONFLICT")
    cleaned = workspace.cleanup_job(job.job_id)
    cleaned = workspace.cleanup_staged_attempt(
        action_id=job.action_id, attempt_id=job.attempt_id,
    ) and cleaned
    if not cleaned:
        result = await jobs.record_cleanup(
            job_id=job.job_id,
            reconciliation_token=job.reconciliation_token,
            expected_version=job.state_version,
            cleanup_status="unknown",
            cleanup_evidence={"kind": "SANDBOX_CLEANUP_UNPROVEN"},
        )
        return result.outcome.value
    if list((job.partial_effects or {}).get("items", [])):
        cleanup = await jobs.record_cleanup(
            job_id=job.job_id,
            reconciliation_token=job.reconciliation_token,
            expected_version=job.state_version,
            cleanup_status="completed",
            cleanup_evidence={"kind": "SANDBOX_PARTIAL_CLEANED"},
        )
        if cleanup.job is None:
            raise RuntimeError("SANDBOX_JOB_READBACK_REQUIRED")
        job = cleanup.job
    resolved = await jobs.resolve_reconciliation(
        job_id=job.job_id,
        reconciliation_token=job.reconciliation_token,
        expected_version=job.state_version,
        resolution=str(resolution), terminal_reason=reason,
        receipt_hash=digest, receipt=receipt,
    )
    if resolved.outcome.value in {
        "succeeded", "failed", "timed_out", "cancelled",
    }:
        workspace.cleanup_terminal_checkpoint(job.job_id)
    return resolved.outcome.value
