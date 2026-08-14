from dataclasses import replace
from datetime import datetime, timezone

import pytest

from services.agent.runtime.domain.sandbox_job import (
    SandboxCleanupStatus, SandboxJobStatus,
)
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobOutcome, SandboxJobReceipt,
)
from services.agent.runtime.sandbox.worker import SandboxJobWorker
from services.agent.runtime.sandbox.workspace import SandboxWorkspaceStore
from tests.test_agent_runtime_sandbox_worker import _Jobs, _Launcher


class _CancelJobs(_Jobs):
    async def claim(self, **_kwargs):
        return SandboxJobReceipt(outcome=SandboxJobOutcome.NOT_FOUND)

    async def claim_recoverable(self, **_kwargs):
        return SandboxJobReceipt(outcome=SandboxJobOutcome.NOT_FOUND)

    async def claim_cancel(self, **_kwargs):
        self.job = replace(
            self.job, status=SandboxJobStatus.CANCEL_REQUESTED,
            state_version=2, fencing_token=1,
            claim_token="55555555-5555-5555-5555-555555555555",
            cancel_requested_at=datetime.now(timezone.utc),
            cancel_accepted_at=datetime.now(timezone.utc),
            cancel_confirmed_at=datetime.now(timezone.utc),
        )
        return SandboxJobReceipt(
            outcome=SandboxJobOutcome.CLAIMED, job=self.job,
        )


@pytest.mark.asyncio
async def test_queued_cancel_is_proven_by_worker_without_launch(tmp_path) -> None:
    jobs, launcher = _CancelJobs(), _Launcher()
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    await workspace.stage_code(
        action_id=jobs.job.action_id, attempt_id=jobs.job.attempt_id,
        content=b"print(1)", expected_sha256=jobs.job.code_sha256,
    )
    result = await SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id="cancel-worker",
    ).run_once()
    assert result.outcome == "cancelled"
    assert jobs.finished == "cancelled"
    assert launcher.launched == 0
    assert not (
        tmp_path / "inputs" / jobs.job.action_id / jobs.job.attempt_id
    ).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", (
    SandboxCleanupStatus.NOT_REQUIRED,
    SandboxCleanupStatus.UNKNOWN,
))
async def test_unstarted_cancel_recovers_after_worker_crash(
    tmp_path, cleanup: SandboxCleanupStatus,
) -> None:
    jobs = _Jobs()
    jobs.job = replace(
        jobs.job, status=SandboxJobStatus.UNKNOWN,
        cancel_requested_at=datetime.now(timezone.utc),
        cancel_accepted_at=datetime.now(timezone.utc),
        cancel_confirmed_at=datetime.now(timezone.utc),
        cleanup_status=cleanup,
        partial_effects={"schema_revision": 1, "items": []},
    )
    result = await SandboxJobWorker(
        jobs=jobs, launcher=_Launcher(),
        workspace=SandboxWorkspaceStore(tmp_path.resolve()),
        worker_id="restart-worker",
    ).reconcile_next()
    assert result.outcome == "cancelled"
    assert jobs.finished == "cancelled"
    assert jobs.job.cleanup_status is (
        SandboxCleanupStatus.NOT_REQUIRED
        if cleanup is SandboxCleanupStatus.NOT_REQUIRED
        else SandboxCleanupStatus.COMPLETED
    )
