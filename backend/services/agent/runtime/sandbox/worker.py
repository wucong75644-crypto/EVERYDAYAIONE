"""Dedicated durable Sandbox Job Worker orchestration."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

from loguru import logger

from services.agent.runtime.domain.sandbox_job import SandboxJobStatus
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobOutcome,
    SandboxJobRepositoryPort,
)

from .contracts import SandboxResourceLimits
from .checkpoint import resolve_terminal_checkpoint
from .cancel_handoff import (
    finish_active_cancel, finish_unstarted_cancel,
    reconcile_unstarted_cancel,
)
from .launcher import IsolationProbe, SandboxLauncherPort, SandboxLaunchRequest
from .receipt import build_receipt
from .workspace import SandboxWorkspaceStore


@dataclass(frozen=True, kw_only=True)
class WorkerCycleResult:
    worked: bool
    outcome: str
    job_id: str | None = None


class SandboxJobWorker:
    """Only execution owner; API and conversation processes never instantiate it."""

    def __init__(
        self, *, jobs: SandboxJobRepositoryPort,
        launcher: SandboxLauncherPort, workspace: SandboxWorkspaceStore,
        worker_id: str, lease_seconds: int = 60,
    ) -> None:
        self._jobs = jobs
        self._launcher = launcher
        self._workspace = workspace
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._draining = False

    def drain(self) -> None:
        self._draining = True

    @property
    def draining(self) -> bool:
        return self._draining

    def probe(self) -> IsolationProbe:
        """Expose the launcher's single authoritative readiness probe."""
        return self._launcher.probe()

    def cleanup_expired_partials(self, retention_seconds: int = 86400) -> int:
        return len(self._workspace.cleanup_expired_quarantine(
            retention_seconds,
        ))

    async def run_once(self) -> WorkerCycleResult:
        if self._draining:
            return WorkerCycleResult(worked=False, outcome="draining")
        probe = self.probe()
        if not probe.ready:
            return WorkerCycleResult(worked=False, outcome=probe.code)
        claimed = await self._jobs.claim(
            worker_id=self._worker_id, lease_seconds=self._lease_seconds,
        )
        if claimed.outcome is SandboxJobOutcome.NOT_FOUND:
            claimed = await self._jobs.claim_recoverable(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
            )
        if claimed.outcome is SandboxJobOutcome.NOT_FOUND:
            claimed = await self._jobs.claim_cancel(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
            )
        if claimed.outcome is SandboxJobOutcome.NOT_FOUND:
            return WorkerCycleResult(worked=False, outcome="idle")
        if claimed.outcome is not SandboxJobOutcome.CLAIMED or claimed.job is None:
            return WorkerCycleResult(worked=False, outcome="claim_rejected")
        job = claimed.job
        try:
            if job.status is SandboxJobStatus.CANCEL_REQUESTED:
                outcome = await finish_unstarted_cancel(self, job)
                return WorkerCycleResult(
                    worked=True, outcome=outcome, job_id=job.job_id,
                )
            return await self._execute(job)
        except Exception as error:
            logger.exception(
                "SANDBOX_WORKER_EXECUTION_FAILED | sandbox_job_id={}"
                " | error_type={}", job.job_id, type(error).__name__,
            )
            try:
                await self._record_unproven_process(
                    job, "SANDBOX_WORKER_EXCEPTION",
                )
            except Exception:
                logger.error(
                    "SANDBOX_WORKER_STATE_PERSIST_UNAVAILABLE"
                    " | sandbox_job_id={}",
                    job.job_id,
                )
            return WorkerCycleResult(
                worked=True, outcome="unknown", job_id=job.job_id,
            )

    async def reconcile_next(self) -> WorkerCycleResult:
        """Durably discover reconciliation work without a local Job index."""
        claimed = await self._jobs.claim_next_reconciliation(
            worker_id=self._worker_id, lease_seconds=self._lease_seconds,
        )
        if claimed.outcome is SandboxJobOutcome.NOT_FOUND:
            return WorkerCycleResult(
                worked=False, outcome="reconcile_idle",
            )
        return await self._reconcile_claimed(_job(claimed))

    async def _reconcile_claimed(self, job) -> WorkerCycleResult:
        job_id = job.job_id
        if (
            job.cancel_confirmed_at is not None
            and job.starting_at is None
            and job.started_at is None
        ):
            outcome = await reconcile_unstarted_cancel(self, job)
            return WorkerCycleResult(
                worked=True, outcome=outcome, job_id=job.job_id,
            )
        checkpoint = self._workspace.read_terminal_checkpoint(job_id)
        if checkpoint is not None:
            outcome = await resolve_terminal_checkpoint(
                jobs=self._jobs, workspace=self._workspace,
                job=job, checkpoint=checkpoint,
            )
            return WorkerCycleResult(
                worked=True, outcome=outcome, job_id=job_id,
            )
        result = await self._launcher.query(job_id)
        if result is None or not result.process_tree_terminated:
            resolved = await self._jobs.resolve_reconciliation(
                job_id=job_id,
                reconciliation_token=_reconciliation(job),
                expected_version=job.state_version,
                resolution="still_unknown",
                terminal_reason="EXECUTION_STATE_UNPROVEN",
                receipt_hash="0" * 64, receipt={},
            )
            return WorkerCycleResult(
                worked=True, outcome=resolved.outcome.value, job_id=job_id,
            )
        partial_items = list((job.partial_effects or {}).get("items", []))
        _, output_dir = self._workspace.prepare_job(job_id)
        limits = SandboxResourceLimits.from_request(job.resource_limits)
        if result.outcome == "succeeded":
            artifacts = await self._workspace.materialize_outputs(
                job_id=job_id, output_dir=output_dir,
                max_bytes=limits.disk_bytes, max_files=limits.file_count,
            )
            digest, receipt = build_receipt(
                execution_outcome="success", stdout=result.stdout,
                stderr=result.stderr, artifacts=artifacts,
                partials=partial_items, materialized=True, cleaned=True,
            )
            resolution, reason = "succeeded", "RECONCILED_SUCCEEDED"
        elif result.outcome == "resource_limit":
            digest, receipt = build_receipt(
                execution_outcome="error", stdout=result.stdout,
                stderr=result.stderr, partials=(), cleaned=True,
            )
            resolution, reason = "failed", "RECONCILED_RESOURCE_LIMIT"
        else:
            if not partial_items:
                return WorkerCycleResult(
                    worked=True, outcome="still_unknown", job_id=job_id,
                )
            digest, receipt = build_receipt(
                execution_outcome=(
                    "timeout" if result.outcome == "timed_out" else "error"
                ),
                stdout=result.stdout, stderr=result.stderr,
                partials=partial_items, cleaned=True,
            )
            resolution = (
                "timed_out" if result.outcome == "timed_out" else "failed"
            )
            reason = f"RECONCILED_{resolution.upper()}"
        await self._write_checkpoint(
            job, resolution, reason, digest, receipt,
        )
        outcome = await resolve_terminal_checkpoint(
            jobs=self._jobs, workspace=self._workspace,
            job=job, checkpoint=self._workspace.read_terminal_checkpoint(
                job_id,
            ) or {},
        )
        return WorkerCycleResult(
            worked=True, outcome=outcome, job_id=job_id,
        )

    async def _execute(self, job) -> WorkerCycleResult:
        code = self._workspace.read_code(
            action_id=job.action_id, attempt_id=job.attempt_id,
            expected_sha256=job.code_sha256,
        )
        input_dir, output_dir = self._workspace.prepare_job(job.job_id)
        self._workspace.materialize_inputs(
            action_id=job.action_id,
            attempt_id=job.attempt_id,
            manifest=dict(job.input_manifest),
            input_dir=input_dir,
        )
        starting = await self._jobs.mark_started(
            job_id=job.job_id, claim_token=_claim(job),
            fencing_token=job.fencing_token,
            expected_version=job.state_version, phase="starting",
        )
        job = _job(starting)
        limits = SandboxResourceLimits.from_request(job.resource_limits)
        process = await self._launcher.launch(SandboxLaunchRequest(
            job_id=job.job_id, code=code, input_dir=input_dir,
            output_dir=output_dir, limits=limits,
        ))
        try:
            running = await self._jobs.mark_started(
                job_id=job.job_id, claim_token=_claim(job),
                fencing_token=job.fencing_token,
                expected_version=job.state_version, phase="running",
            )
            job = _job(running)
            result, job, cancelled = await self._wait(job, process)
        except Exception:
            await process.request_cancel()
            await process.prove_terminated()
            raise
        if cancelled:
            outcome = await finish_active_cancel(self, job, process, result)
            return WorkerCycleResult(
                worked=True, outcome=outcome, job_id=job.job_id,
            )
        if result.outcome != "succeeded":
            logger.error(
                "SANDBOX_EXECUTION_RESULT | sandbox_job_id={} | outcome={}"
                " | exit_code={} | stdout_length={} | stderr_length={}"
                " | stderr_sha256={}",
                job.job_id, result.outcome, result.exit_code,
                len(result.stdout), len(result.stderr),
                hashlib.sha256(result.stderr).hexdigest(),
            )
        if (
            not result.process_tree_terminated
            or not await process.prove_terminated()
        ):
            await self._record_unproven_process(
                job, "SANDBOX_PROCESS_TREE_UNPROVEN",
            )
            return WorkerCycleResult(
                worked=True, outcome="unknown", job_id=job.job_id,
            )
        return await self._finish(job, result, output_dir, limits)

    async def _wait(self, job, process):
        task = asyncio.create_task(process.wait())
        interval = max(1.0, min(10.0, self._lease_seconds / 3))
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=interval)
            latest = await self._jobs.get_owned(
                job_id=job.job_id, worker_id=self._worker_id,
                claim_token=_claim(job), fencing_token=job.fencing_token,
            )
            if latest.job is None:
                task.cancel()
                raise RuntimeError("SANDBOX_JOB_READBACK_REQUIRED")
            job = latest.job
            if job.status is SandboxJobStatus.CANCEL_REQUESTED:
                if done:
                    result = await task
                else:
                    task.cancel()
                    result = type("_Pending", (), {
                        "stdout": b"", "stderr": b"", "outcome": "failed",
                    })()
                return result, job, True
            if done:
                break
            renewed = await self._jobs.renew(
                job_id=job.job_id, claim_token=_claim(job),
                fencing_token=job.fencing_token,
                expected_version=job.state_version,
                lease_seconds=self._lease_seconds,
            )
            job = _job(renewed)
        return await task, job, False

    async def _finish(self, job, result, output_dir, limits) -> WorkerCycleResult:
        if result.outcome == "succeeded":
            artifacts = await self._workspace.materialize_outputs(
                job_id=job.job_id, output_dir=output_dir,
                max_bytes=limits.disk_bytes, max_files=limits.file_count,
            )
            digest, receipt = build_receipt(
                execution_outcome="success", stdout=result.stdout,
                stderr=result.stderr, artifacts=artifacts, materialized=True,
            )
            status, reason = "succeeded", "EXECUTION_SUCCEEDED"
        elif result.outcome == "resource_limit":
            partials = ()
            digest, receipt = build_receipt(
                execution_outcome="error", stdout=result.stdout,
                stderr=result.stderr, partials=partials, cleaned=True,
            )
            status, reason = "failed", "EXECUTION_RESOURCE_LIMIT"
        else:
            partials = self._workspace.quarantine(
                job.job_id, output_dir,
                max_bytes=limits.disk_bytes, max_files=limits.file_count,
            )
            digest, receipt = build_receipt(
                execution_outcome=(
                    "timeout" if result.outcome == "timed_out" else "error"
                ),
                stdout=result.stdout, stderr=result.stderr,
                partials=partials, cleaned=True,
            )
            status = "timed_out" if result.outcome == "timed_out" else "failed"
            reason = (
                "EXECUTION_TIMED_OUT"
                if status == "timed_out" else "EXECUTION_FAILED"
            )
        await self._write_checkpoint(job, status, reason, digest, receipt)
        if result.outcome != "succeeded" and partials:
            await self._mark_unknown(
                job, "SANDBOX_TERMINAL_PENDING_CLEANUP",
                partials=partials,
            )
            return WorkerCycleResult(
                worked=True, outcome="unknown", job_id=job.job_id,
            )
        if (
            not self._workspace.cleanup_job(job.job_id)
            or not self._cleanup_inputs(job)
        ):
            await self._mark_unknown(
                job, "SANDBOX_TERMINAL_CLEANUP_UNPROVEN",
                partials=partials if result.outcome != "succeeded" else (),
            )
            return WorkerCycleResult(
                worked=True, outcome="unknown", job_id=job.job_id,
            )
        terminal = await self._jobs.finish(
            job_id=job.job_id, claim_token=_claim(job),
            fencing_token=job.fencing_token,
            expected_version=job.state_version,
            terminal_status=status, terminal_reason=reason,
            receipt_hash=digest, receipt=receipt,
        )
        if terminal.outcome.value in {status, "already_terminal"}:
            self._workspace.cleanup_terminal_checkpoint(job.job_id)
        return WorkerCycleResult(
            worked=True, outcome=terminal.outcome.value, job_id=job.job_id,
        )

    async def _finish_cancel(self, job, process, result) -> WorkerCycleResult:
        """Compatibility seam; cancellation ownership lives in cancel_handoff."""
        outcome = await finish_active_cancel(self, job, process, result)
        return WorkerCycleResult(
            worked=True, outcome=outcome, job_id=job.job_id,
        )

    async def _write_checkpoint(
        self, job, resolution: str, reason: str,
        digest: str, receipt: dict[str, object],
    ) -> None:
        await self._workspace.write_terminal_checkpoint(
            job_id=job.job_id,
            checkpoint={
                "schema_revision": 1,
                "sandbox_job_id": job.job_id,
                "resolution": resolution,
                "terminal_reason": reason,
                "receipt_hash": digest,
                "receipt": receipt,
            },
        )

    async def _mark_unknown(
        self, job, reason: str, *, partials=None,
    ) -> None:
        if partials is None:
            _, output_dir = self._workspace.prepare_job(job.job_id)
            limits = SandboxResourceLimits.from_request(job.resource_limits)
            partials = self._workspace.quarantine(
                job.job_id, output_dir,
                max_bytes=limits.disk_bytes, max_files=limits.file_count,
            )
        await self._jobs.record_unknown(
            job_id=job.job_id, claim_token=_claim(job),
            fencing_token=job.fencing_token,
            expected_version=job.state_version,
            ambiguity_evidence={"kind": reason},
            partial_effects={"schema_revision": 1, "items": list(partials)},
        )

    async def _record_unproven_process(self, job, reason: str) -> None:
        await self._jobs.record_unknown(
            job_id=job.job_id, claim_token=_claim(job),
            fencing_token=job.fencing_token,
            expected_version=job.state_version,
            ambiguity_evidence={"kind": reason},
            partial_effects={"schema_revision": 1, "items": []},
        )

    async def _record_cleanup_unknown(self, job) -> None:
        logger.error(
            "SANDBOX_CLEANUP_UNPROVEN | sandbox_job_id={}",
            job.job_id,
        )
        await self._jobs.record_cleanup(
            job_id=job.job_id,
            reconciliation_token=_reconciliation(job),
            expected_version=job.state_version,
            cleanup_status="unknown",
            cleanup_evidence={"kind": "SANDBOX_CLEANUP_UNPROVEN"},
        )

    def _cleanup_inputs(self, job) -> bool:
        cleaned = self._workspace.cleanup_staged_attempt(
            action_id=job.action_id, attempt_id=job.attempt_id,
        )
        if not cleaned:
            logger.error(
                "SANDBOX_INPUT_CLEANUP_UNPROVEN | sandbox_job_id={}",
                job.job_id,
            )
        return cleaned


def _claim(job) -> str:
    if not job.claim_token:
        raise RuntimeError("SANDBOX_CLAIM_TOKEN_REQUIRED")
    return job.claim_token


def _job(receipt):
    if receipt.job is None:
        raise RuntimeError("SANDBOX_JOB_READBACK_REQUIRED")
    return receipt.job


def _reconciliation(job) -> str:
    if not job.reconciliation_token:
        raise RuntimeError("SANDBOX_RECONCILIATION_TOKEN_REQUIRED")
    return job.reconciliation_token
