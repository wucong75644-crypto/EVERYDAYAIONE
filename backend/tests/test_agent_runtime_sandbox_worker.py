from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from services.agent.runtime.sandbox.launcher import IsolationProbe
from services.agent.runtime.sandbox.launcher import SandboxLaunchResult
from services.agent.runtime.sandbox.contracts import SandboxResourceLimits
from services.agent.runtime.domain.sandbox_job import (
    SandboxCleanupStatus,
    SandboxJobSnapshot,
    SandboxJobStatus,
    SandboxMaterializationStatus,
)
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobOutcome,
    SandboxJobReceipt,
)
from services.agent.runtime.sandbox.worker import SandboxJobWorker
from services.agent.runtime.sandbox.workspace import SandboxWorkspaceStore


class _UnavailableLauncher:
    def probe(self):
        return IsolationProbe(
            ready=False, code="SANDBOX_NSJAIL_REQUIRED",
        )

    async def launch(self, _request):
        raise AssertionError("launch must remain unreachable")

    async def query(self, _job_id):
        raise AssertionError("query must remain unreachable")


def test_worker_probe_delegates_to_launcher(tmp_path) -> None:
    launcher = _UnavailableLauncher()
    worker = SandboxJobWorker(
        jobs=type("_Jobs", (), {})(), launcher=launcher,
        workspace=SandboxWorkspaceStore(tmp_path.resolve()), worker_id="worker-1",
    )
    assert worker.probe() == launcher.probe()


@pytest.mark.asyncio
async def test_worker_does_not_claim_when_isolation_probe_fails(
    tmp_path,
) -> None:
    jobs = type("_Jobs", (), {"claim": AsyncMock(), "claim_recoverable": AsyncMock()})()
    worker = SandboxJobWorker(
        jobs=jobs, launcher=_UnavailableLauncher(),
        workspace=SandboxWorkspaceStore(tmp_path.resolve()),
        worker_id="worker-1",
    )
    result = await worker.run_once()
    assert not result.worked
    assert result.outcome == "SANDBOX_NSJAIL_REQUIRED"
    for method in ("claim", "claim_recoverable"):
        getattr(jobs, method).assert_not_awaited()

@pytest.mark.asyncio
async def test_draining_worker_never_claims(tmp_path) -> None:
    jobs = type("_Jobs", (), {"claim": AsyncMock(), "claim_recoverable": AsyncMock()})()
    worker = SandboxJobWorker(
        jobs=jobs, launcher=_UnavailableLauncher(),
        workspace=SandboxWorkspaceStore(tmp_path.resolve()),
        worker_id="worker-1",
    )
    worker.drain()
    result = await worker.run_once()
    assert result.outcome == "draining"
    for method in ("claim", "claim_recoverable"):
        getattr(jobs, method).assert_not_awaited()

def _job(status=SandboxJobStatus.QUEUED):
    return SandboxJobSnapshot(
        job_id="11111111-1111-1111-1111-111111111111",
        action_id="22222222-2222-2222-2222-222222222222",
        attempt_id="33333333-3333-3333-3333-333333333333",
        dispatch_intent_id="44444444-4444-4444-4444-444444444444",
        external_idempotency_key="action:key", request_hash="a" * 64,
        code_sha256=hashlib.sha256(b"print(1)").hexdigest(),
        input_manifest={"schema_revision": 1, "items": []},
        resource_limits=SandboxResourceLimits.from_request({}).as_dict(),
        status=status, state_version=1, fencing_token=0,
        cleanup_status=SandboxCleanupStatus.NOT_REQUIRED,
        materialization_status=SandboxMaterializationStatus.NOT_STARTED,
        queued_at=datetime.now(timezone.utc),
        artifact_manifest={"schema_revision": 1, "items": []},
        partial_effects={"schema_revision": 1, "items": []},
    )


class _Jobs:
    def __init__(self):
        self.job = _job()
        self.finished = None
        self.unknown = False
        self.finish_outcome = None
        self.resolve_outcome = None

    async def claim(self, **_kwargs):
        self.job = replace(
            self.job, status=SandboxJobStatus.CLAIMED,
            state_version=2, fencing_token=1,
            claim_token="55555555-5555-5555-5555-555555555555",
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.CLAIMED, job=self.job,
        )

    async def mark_started(self, *, phase, **_kwargs):
        self.job = replace(
            self.job,
            status=(
                SandboxJobStatus.STARTING
                if phase == "starting" else SandboxJobStatus.RUNNING
            ),
            state_version=self.job.state_version + 1,
        )
        return SandboxJobReceipt(
            outcome=(
                SandboxJobOutcome.STARTING
                if phase == "starting" else SandboxJobOutcome.RUNNING
            ),
            job=self.job,
        )

    async def get(self, **_kwargs):
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.FOUND, job=self.job,
        )

    async def get_owned(self, **_kwargs):
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.FOUND, job=self.job,
        )

    async def renew(self, **_kwargs):
        self.job = replace(self.job, state_version=self.job.state_version + 1)
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.RENEWED, job=self.job,
        )

    async def finish(self, *, terminal_status, **_kwargs):
        if self.finish_outcome is not None:
            return SandboxJobReceipt(outcome=self.finish_outcome, job=self.job)
        self.finished = terminal_status
        self.job = replace(
            self.job, status=SandboxJobStatus(terminal_status),
            state_version=self.job.state_version + 1,
            terminal_at=datetime.now(timezone.utc),
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome(terminal_status), job=self.job,
        )

    async def record_unknown(self, **kwargs):
        self.unknown = True
        self.job = replace(
            self.job, status=SandboxJobStatus.UNKNOWN,
            state_version=self.job.state_version + 1,
            partial_effects=kwargs.get(
                "partial_effects", self.job.partial_effects,
            ),
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.UNKNOWN, job=self.job,
        )

    async def record_cancel_signal(self, *, signal_state, **_kwargs):
        self.job = replace(
            self.job, state_version=self.job.state_version + 1,
        )
        return SandboxJobReceipt(
            outcome=(
                SandboxJobOutcome.CANCEL_ACCEPTED
                if signal_state == "accepted"
                else SandboxJobOutcome.CANCEL_CONFIRMED
            ),
            job=self.job,
        )

    async def claim_next_reconciliation(self, **_kwargs):
        self.job = replace(
            self.job, state_version=self.job.state_version + 1,
            reconciliation_token="66666666-6666-6666-6666-666666666666",
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.CLAIMED, job=self.job,
        )

    async def record_cleanup(self, **_kwargs):
        self.job = replace(
            self.job, state_version=self.job.state_version + 1,
            cleanup_status=SandboxCleanupStatus.COMPLETED,
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.CLEANUP_COMPLETED, job=self.job,
        )

    async def record_reconciled_partials(self, *, partial_effects, **_kwargs):
        self.job = replace(
            self.job, state_version=self.job.state_version + 1,
            partial_effects=partial_effects,
            cleanup_status=SandboxCleanupStatus.PENDING,
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.PARTIALS_RECORDED, job=self.job,
        )

    async def resolve_reconciliation(self, *, resolution, **_kwargs):
        if self.resolve_outcome is not None:
            return SandboxJobReceipt(
                outcome=self.resolve_outcome, job=self.job,
            )
        self.finished = resolution
        self.job = replace(
            self.job, status=SandboxJobStatus(resolution),
            state_version=self.job.state_version + 1,
            terminal_at=datetime.now(timezone.utc),
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome(resolution), job=self.job,
        )


class _Process:
    def __init__(self, *, outcome="succeeded", terminated=True):
        self.outcome = outcome
        self.terminated = terminated

    async def wait(self):
        return SandboxLaunchResult(
            outcome=self.outcome, stdout=b"ok", stderr=b"",
            exit_code=0, process_tree_terminated=self.terminated,
        )

    async def request_cancel(self):
        return True

    async def prove_terminated(self):
        return self.terminated


class _Launcher:
    def __init__(self, *, fail=False, process=None, query_result=None):
        self.fail = fail
        self.process = process or _Process()
        self.query_result = query_result
        self.launched = 0

    def probe(self):
        return IsolationProbe(ready=True, code="SANDBOX_ISOLATION_READY")

    async def launch(self, _request):
        self.launched += 1
        if self.fail:
            raise RuntimeError("synthetic")
        return self.process

    async def query(self, _job_id):
        return self.query_result


@pytest.mark.asyncio
async def test_worker_materializes_and_finishes_once(tmp_path) -> None:
    jobs, launcher = _Jobs(), _Launcher()
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    await workspace.stage_code(
        action_id=jobs.job.action_id, attempt_id=jobs.job.attempt_id,
        content=b"print(1)",
        expected_sha256=jobs.job.code_sha256,
    )
    worker = SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id="worker-1",
    )
    result = await worker.run_once()
    assert result.outcome == "succeeded"
    assert jobs.finished == "succeeded"
    assert launcher.launched == 1
    assert not (
        tmp_path / "inputs" / jobs.job.action_id / jobs.job.attempt_id
    ).exists()


@pytest.mark.asyncio
async def test_materialized_checkpoint_survives_worker_restart(
    tmp_path, monkeypatch,
) -> None:
    jobs, launcher = _Jobs(), _Launcher()
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    await workspace.stage_code(
        action_id=jobs.job.action_id, attempt_id=jobs.job.attempt_id,
        content=b"print(1)", expected_sha256=jobs.job.code_sha256,
    )
    _, output = workspace.prepare_job(jobs.job.job_id)
    (output / "result.bin").write_bytes(b"result")
    original_cleanup = workspace.cleanup_job
    monkeypatch.setattr(workspace, "cleanup_job", lambda _job_id: False)
    first = SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id="worker-1",
    )
    result = await first.run_once()
    assert result.outcome == "unknown"
    assert jobs.finished is None
    checkpoint = workspace.read_terminal_checkpoint(jobs.job.job_id)
    assert checkpoint is not None
    assert checkpoint["resolution"] == "succeeded"

    monkeypatch.setattr(workspace, "cleanup_job", original_cleanup)
    restarted = SandboxJobWorker(
        jobs=jobs, launcher=_Launcher(), workspace=workspace,
        worker_id="worker-2",
    )
    reconciled = await restarted.reconcile_next()
    assert reconciled.outcome == "succeeded"
    assert jobs.finished == "succeeded"
    assert workspace.read_terminal_checkpoint(jobs.job.job_id) is None


@pytest.mark.asyncio
async def test_finish_ownership_loss_preserves_checkpoint(tmp_path) -> None:
    jobs, launcher = _Jobs(), _Launcher()
    jobs.finish_outcome = SandboxJobOutcome.OWNERSHIP_LOST
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    await workspace.stage_code(
        action_id=jobs.job.action_id, attempt_id=jobs.job.attempt_id,
        content=b"print(1)", expected_sha256=jobs.job.code_sha256,
    )
    result = await SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id="worker-1",
    ).run_once()
    assert result.outcome == "ownership_lost"
    assert jobs.finished is None
    assert workspace.read_terminal_checkpoint(jobs.job.job_id) is not None


@pytest.mark.asyncio
async def test_query_terminal_writes_checkpoint_before_resolve(tmp_path) -> None:
    jobs = _Jobs()
    jobs.resolve_outcome = SandboxJobOutcome.OWNERSHIP_LOST
    jobs.job = replace(
        jobs.job, status=SandboxJobStatus.UNKNOWN,
        reconciliation_token="66666666-6666-6666-6666-666666666666",
    )
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    _, output = workspace.prepare_job(jobs.job.job_id)
    (output / "result.bin").write_bytes(b"result")
    launcher = _Launcher(query_result=SandboxLaunchResult(
        outcome="succeeded", stdout=b"ok", stderr=b"",
        process_tree_terminated=True,
    ))
    result = await SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id="worker-2",
    )._reconcile_claimed(jobs.job)
    assert result.outcome == "ownership_lost"
    checkpoint = workspace.read_terminal_checkpoint(jobs.job.job_id)
    assert checkpoint is not None
    assert checkpoint["resolution"] == "succeeded"


@pytest.mark.asyncio
async def test_launch_ambiguity_becomes_unknown_without_relaunch(
    tmp_path,
) -> None:
    jobs, launcher = _Jobs(), _Launcher(fail=True)
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    await workspace.stage_code(
        action_id=jobs.job.action_id, attempt_id=jobs.job.attempt_id,
        content=b"print(1)",
        expected_sha256=jobs.job.code_sha256,
    )
    worker = SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id="worker-1",
    )
    result = await worker.run_once()
    assert result.outcome == "unknown"
    assert jobs.unknown
    assert launcher.launched == 1


@pytest.mark.asyncio
async def test_resource_limit_discards_oversized_outputs_and_finishes(
    tmp_path,
) -> None:
    jobs = _Jobs()
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    await workspace.stage_code(
        action_id=jobs.job.action_id, attempt_id=jobs.job.attempt_id,
        content=b"print(1)", expected_sha256=jobs.job.code_sha256,
    )
    _, output = workspace.prepare_job(jobs.job.job_id)
    for index in range(101):
        (output / f"{index:03d}").write_bytes(b"x")
    result = await SandboxJobWorker(
        jobs=jobs,
        launcher=_Launcher(process=_Process(outcome="resource_limit")),
        workspace=workspace, worker_id="worker-1",
    ).run_once()
    assert result.outcome == "failed"
    assert jobs.finished == "failed"
    assert not (tmp_path / "jobs" / jobs.job.job_id).exists()


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "timed_out"])
@pytest.mark.asyncio
async def test_unproven_process_tree_never_materializes_or_finishes(
    tmp_path, outcome,
) -> None:
    jobs = _Jobs()
    launcher = _Launcher(
        process=_Process(outcome=outcome, terminated=False),
    )
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    await workspace.stage_code(
        action_id=jobs.job.action_id, attempt_id=jobs.job.attempt_id,
        content=b"print(1)", expected_sha256=jobs.job.code_sha256,
    )
    _, output = workspace.prepare_job(jobs.job.job_id)
    (output / "racing.bin").write_bytes(b"still-writing")
    worker = SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id="worker-1",
    )
    result = await worker.run_once()
    assert result.outcome == "unknown"
    assert jobs.finished is None
    assert not (tmp_path / "objects").exists()


@pytest.mark.asyncio
async def test_cancel_requires_process_tree_proof_and_records_partials(
    tmp_path,
) -> None:
    jobs = _Jobs()
    jobs.job = replace(
        jobs.job, status=SandboxJobStatus.CANCEL_REQUESTED,
        claim_token="55555555-5555-5555-5555-555555555555",
        fencing_token=1,
    )
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    _, output = workspace.prepare_job(jobs.job.job_id)
    (output / "partial.bin").write_bytes(b"partial")
    worker = SandboxJobWorker(
        jobs=jobs, launcher=_Launcher(), workspace=workspace,
        worker_id="worker-1",
    )
    result = await worker._finish_cancel(
        jobs.job, _Process(),
        SandboxLaunchResult(
            outcome="failed", stdout=b"", stderr=b"",
            process_tree_terminated=True,
        ),
    )
    assert result.outcome == "unknown"
    assert jobs.finished is None
    assert workspace.read_terminal_checkpoint(jobs.job.job_id) is not None

    restarted = SandboxJobWorker(
        jobs=jobs, launcher=_Launcher(), workspace=workspace,
        worker_id="worker-2",
    )
    reconciled = await restarted.reconcile_next()
    assert reconciled.outcome == "cancelled"
    assert jobs.finished == "cancelled"
    assert workspace.read_terminal_checkpoint(jobs.job.job_id) is None
    assert not (tmp_path / "jobs" / jobs.job.job_id).exists()


@pytest.mark.asyncio
async def test_checkpoint_partials_freeze_before_recovery_cleanup(
    tmp_path,
) -> None:
    jobs = _Jobs()
    jobs.job = replace(
        jobs.job, status=SandboxJobStatus.UNKNOWN,
        reconciliation_token="66666666-6666-6666-6666-666666666666",
        partial_effects={"schema_revision": 1, "items": []},
    )
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    _, output = workspace.prepare_job(jobs.job.job_id)
    (output / "partial.bin").write_bytes(b"partial")
    partials = workspace.quarantine(
        jobs.job.job_id, output, max_bytes=1024, max_files=2,
    )
    from services.agent.runtime.sandbox.receipt import build_receipt

    digest, receipt = build_receipt(
        execution_outcome="error", stdout=b"", stderr=b"",
        partials=partials, cleaned=True,
    )
    await workspace.write_terminal_checkpoint(
        job_id=jobs.job.job_id,
        checkpoint={
            "schema_revision": 1,
            "sandbox_job_id": jobs.job.job_id,
            "resolution": "failed",
            "terminal_reason": "EXECUTION_FAILED",
            "receipt_hash": digest,
            "receipt": receipt,
        },
    )
    reconciled = await SandboxJobWorker(
        jobs=jobs, launcher=_Launcher(), workspace=workspace,
        worker_id="worker-2",
    )._reconcile_claimed(jobs.job)
    assert reconciled.outcome == "failed"
    assert jobs.finished == "failed"
    assert jobs.job.partial_effects["items"] == list(partials)
    assert not (tmp_path / "quarantine" / jobs.job.job_id).exists()
