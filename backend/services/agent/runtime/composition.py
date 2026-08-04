"""Process-exclusive production composition roots."""

from __future__ import annotations

from typing import Any

from core.db_scope import (
    AsyncScopedDatabaseClient, DatabaseAccessKind, DatabaseScope,
)
from services.agent.runtime.application.authorization_recovery import (
    AuthorizationRecoveryDriver,
)
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.application.coordinator import (
    RuntimeCoordinator, RuntimeLoopCoordinator,
)
from services.agent.runtime.application.model_loop import ModelLoopDriver
from services.agent.runtime.executors.resolver import (
    PostgresActionExecutorResolver,
)
from services.agent.runtime.infrastructure.model.adapter import (
    ExistingProviderModelAdapter,
)
from services.agent.runtime.infrastructure.postgres.action_repository import (
    PostgresActionRepository,
)
from services.agent.runtime.application.projection_worker import (
    CompatibilityProjectionWorker,
)
from services.agent.runtime.application.confirmation_notification import (
    ToolConfirmationNotificationWorker,
)
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.sandbox_job import (
    SandboxJobExecutor, register_sandbox_job_executor,
)
from services.agent.runtime.infrastructure.postgres.authorization import (
    PostgresActionAuthorizationRepository,
)
from services.agent.runtime.infrastructure.postgres.command_claim_repository import (
    PostgresCommandClaimRepository,
)
from services.agent.runtime.infrastructure.postgres.coordinator_recovery import (
    PostgresCoordinatorRecoveryRepository,
)
from services.agent.runtime.infrastructure.postgres.model_attempt_repository import (
    PostgresModelAttemptRepository,
)
from services.agent.runtime.infrastructure.postgres.repository import (
    PostgresRuntimeRepository,
)
from services.agent.runtime.infrastructure.postgres.compat_projection import (
    PostgresCompatibilityProjection,
)
from services.agent.runtime.policy.evaluator import PolicyEvaluator
from services.agent.runtime.sandbox.composition import (
    build_sandbox_executor_components, build_sandbox_worker_components,
)
from services.agent.runtime.sandbox.nsjail import (
    NsJailSubprocessLauncher, SandboxWorkerIdentity,
)
from services.agent.runtime.production_model import (
    PostgresModelCallFactory, retain_unknown_model_attempt,
)
from services.agent.runtime.catalog import RuntimeToolCatalog
from services.agent.runtime.catalog.registry import build_runtime_version_registry
from services.agent.runtime.production_composition import build_production_action_loop


class RuntimeOwner:
    def __init__(self, commands, runtime) -> None:
        self.commands = commands
        self.runtime = runtime

    async def run_once(self) -> bool:
        return any((
            await self.commands.run_once(),
            await self.runtime.run_once(),
            await self.runtime.action_once(),
            await self.runtime.reconciliation_once(),
        ))

    def stop(self) -> None:
        self.commands.stop()
        self.runtime.stop()


class ProjectionOwner:
    def __init__(self, projection, confirmations) -> None:
        self.projection = projection
        self.confirmations = confirmations

    async def run_once(self) -> bool:
        projected = await self.projection.run_once()
        notified = await self.confirmations.run_once()
        return bool(projected or notified)


def scoped(database: Any, kind: DatabaseAccessKind, worker_id: str):
    return AsyncScopedDatabaseClient(database, DatabaseScope(
        actor_user_id=None, org_id=None, access_kind=kind,
        request_id=worker_id[:128],
    ))


def build_projection(database: Any, worker_id: str):
    from services.tool_confirmation import tool_confirmation_service
    from services.websocket_manager import ws_manager

    db = scoped(database, DatabaseAccessKind.PROJECTION, worker_id)
    return ProjectionOwner(
        CompatibilityProjectionWorker(PostgresCompatibilityProjection(db)),
        ToolConfirmationNotificationWorker(
            database=db, service=tool_confirmation_service,
            websocket_manager=ws_manager, worker_id=worker_id,
        ),
    )


def build_runtime(database: Any, settings, *, production_components=None) -> RuntimeOwner:
    if not settings.sandbox_runtime_revision:
        raise RuntimeError("SANDBOX_RUNTIME_REVISION_REQUIRED")
    worker_id = settings.agent_runtime_worker_id
    db = scoped(database, DatabaseAccessKind.AGENT_RUNTIME, worker_id)
    runtime_repository = PostgresRuntimeRepository(db)
    recovery = PostgresCoordinatorRecoveryRepository(db)
    actions = PostgresActionRepository(db)
    attempts = PostgresModelAttemptRepository(db)
    registry = ExecutorRegistry()
    sandbox = build_sandbox_executor_components(
        runtime_database=db,
        workspace_root=settings.sandbox_job_root,
        runtime_revision=settings.sandbox_runtime_revision,
        registry=registry,
    )
    versions = build_runtime_version_registry()
    production_enabled = bool(getattr(
        settings, "agent_runtime_production_composition_enabled", False,
    ))
    if production_enabled:
        components = production_components or getattr(
            settings, "agent_runtime_production_components", None,
        )
        if components is None:
            from services.agent.runtime.production_composition import (
                build_production_components_for_worker,
            )
            components = build_production_components_for_worker(
                database=db, settings=settings, sandbox_registry=registry,
            )
        registry = components.registry
        catalog = components.catalog.catalog
    else:
        components = None
        _, catalog = versions.resolve_for_agent(
            settings.agent_runtime_agent_definition_id,
            settings.agent_runtime_agent_definition_revision,
        )
    _assert_runtime_catalog(catalog, settings)
    action_loop = (
        build_production_action_loop(
            database=db, worker_id=worker_id, components=components,
            capability_issuer=sandbox.capability_issuer,
        )
        if components is not None else ActionLoopDriver(
            recovery_repository=recovery,
            action_repository=actions,
            authorization_repository=PostgresActionAuthorizationRepository(db),
            resolver=PostgresActionExecutorResolver(registry),
            worker_id=worker_id,
            capability_issuer=sandbox.capability_issuer,
        )
    )
    credential_broker = getattr(
        getattr(components, "service_bundle", None), "credential_broker", None,
    )
    model_loop = ModelLoopDriver(
        runtime_repository=runtime_repository,
        attempt_repository=attempts,
        action_repository=actions,
        recovery_repository=recovery,
        model=ExistingProviderModelAdapter(db=db),
        call_factory=PostgresModelCallFactory(
            db, worker_id, version_registry=versions,
            credential_broker=credential_broker,
        ),
        reconciler=retain_unknown_model_attempt,
    )
    runtime = RuntimeLoopCoordinator(
        recovery_repository=recovery,
        runtime_repository=runtime_repository,
        model_loop=model_loop,
        action_loop=action_loop,
        worker_id=worker_id,
    )
    commands = RuntimeCoordinator(
        repository=PostgresCommandClaimRepository(db),
        worker_id=worker_id,
        handler=runtime.handle_command,
    )
    return RuntimeOwner(commands, runtime)


def build_authorization(database: Any, worker_id: str):
    db = scoped(database, DatabaseAccessKind.AUTHORIZATION, worker_id)
    registry = ExecutorRegistry()
    register_sandbox_job_executor(registry, SandboxJobExecutor())
    versions = build_runtime_version_registry()
    _assert_runtime_catalog(versions.catalogs.resolve(versions.catalogs.revisions()[0]), None)
    return AuthorizationRecoveryDriver(
        repository=PostgresActionAuthorizationRepository(db),
        registry=registry, evaluator=PolicyEvaluator(), worker_id=worker_id,
    )


def _assert_runtime_catalog(catalog: RuntimeToolCatalog, settings: Any) -> None:
    names = {tool.canonical_name for tool in catalog.definitions()}
    if "code_execute" not in names:
        raise RuntimeError("RUNTIME_CATALOG_NOT_MINIMAL")
    if settings is not None and not getattr(settings, "agent_runtime_release_revision", ""):
        raise RuntimeError("RUNTIME_RELEASE_REVISION_REQUIRED")


def build_sandbox(database: Any, settings):
    identity = SandboxWorkerIdentity.capture_current_process()
    db = scoped(
        database, DatabaseAccessKind.SANDBOX_WORKER,
        settings.agent_runtime_worker_id,
    )
    launcher = NsJailSubprocessLauncher(
        rootfs=settings.sandbox_rootfs,
        python_path=settings.sandbox_python_path,
        seccomp_policy=settings.sandbox_seccomp_policy,
        cgroup_v2_mount=settings.sandbox_cgroup_v2_mount,
        nsjail_path=settings.sandbox_nsjail_path,
        nsjail_sha256=settings.sandbox_nsjail_sha256,
        rootfs_manifest=settings.sandbox_rootfs_manifest,
        rootfs_sha256=settings.sandbox_rootfs_sha256,
        seccomp_sha256=settings.sandbox_seccomp_sha256,
        worker_identity=identity,
    )
    return build_sandbox_worker_components(
        worker_database=db, launcher=launcher,
        workspace_root=settings.sandbox_job_root,
        worker_id=settings.agent_runtime_worker_id,
        worker_identity=identity,
    )
