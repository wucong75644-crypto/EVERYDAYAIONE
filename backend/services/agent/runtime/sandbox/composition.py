"""Explicit Sandbox compositions; intentionally not connected to startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.sandbox_job import (
    SandboxJobExecutor,
    register_sandbox_job_executor,
)
from services.agent.runtime.infrastructure.postgres.sandbox_job_repository import (
    PostgresSandboxJobRepository,
)

from .issuer import SandboxCapabilityIssuer
from .launcher import SandboxLauncherPort
from .service import SandboxJobWorkerService
from .worker import SandboxJobWorker
from .workspace import SandboxWorkspaceStore


@dataclass(frozen=True, kw_only=True)
class SandboxWorkerComponents:
    worker: SandboxJobWorker
    service: SandboxJobWorkerService


@dataclass(frozen=True, kw_only=True)
class SandboxExecutorComponents:
    executor: SandboxJobExecutor
    capability_issuer: SandboxCapabilityIssuer


def build_sandbox_worker_components(
    *, worker_database, launcher: SandboxLauncherPort,
    workspace_root: str | Path, worker_id: str,
) -> SandboxWorkerComponents:
    """Build the only process allowed to own Sandbox execution."""
    workspace = SandboxWorkspaceStore(Path(workspace_root))
    jobs = PostgresSandboxJobRepository(worker_database)
    worker = SandboxJobWorker(
        jobs=jobs, launcher=launcher, workspace=workspace,
        worker_id=worker_id,
    )
    return SandboxWorkerComponents(
        worker=worker, service=SandboxJobWorkerService(worker),
    )


def build_sandbox_executor_components(
    *, runtime_database, workspace_root: str | Path,
    runtime_revision: str, registry: ExecutorRegistry,
) -> SandboxExecutorComponents:
    """Register dispatch/query only; never construct a Worker or launcher."""
    workspace = SandboxWorkspaceStore(Path(workspace_root))
    jobs = PostgresSandboxJobRepository(runtime_database)

    issuer = SandboxCapabilityIssuer(
        jobs=jobs, workspace=workspace,
        runtime_revision=runtime_revision,
    )
    executor = SandboxJobExecutor()
    register_sandbox_job_executor(registry, executor)
    return SandboxExecutorComponents(
        executor=executor, capability_issuer=issuer,
    )
