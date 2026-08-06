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
from services.agent.runtime.infrastructure.postgres.action_repository import (
    PostgresActionRepository,
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
from services.agent.runtime.policy.evaluator import PolicyEvaluator
from services.agent.runtime.sandbox.composition import build_sandbox_worker_components
from services.agent.runtime.sandbox.nsjail import (
    NsJailSubprocessLauncher, SandboxWorkerIdentity,
)
from services.agent.runtime.production_model import (
    PostgresModelCallFactory, retain_unknown_model_attempt,
)
from services.agent.runtime.catalog import RuntimeToolCatalog
from services.agent.runtime.catalog.registry import build_runtime_version_registry
from services.agent.runtime.executors.real_base import RuntimeReadResources


class RuntimeOwner:
    def __init__(self, commands, runtime, *, readiness=None) -> None:
        self.commands = commands
        self.runtime = runtime
        self.readiness = readiness
        self._draining = False

    @property
    def ready(self) -> bool:
        """Object graph construction does not imply production readiness."""
        return self.readiness is None or bool(
            getattr(self.readiness, "ready", False),
        )

    async def run_once(self) -> bool:
        if self._draining:
            return False
        worked = await self.commands.run_once()
        if self._draining:
            return worked
        worked = (await self.runtime.run_once()) or worked
        if self._draining:
            return worked
        worked = (await self.runtime.action_once()) or worked
        if self._draining:
            return worked
        return (await self.runtime.reconciliation_once()) or worked

    def drain(self) -> None:
        """Stop future claims while allowing the current fenced call to finish."""
        if self._draining:
            return
        self._draining = True
        self.commands.stop()
        self.runtime.stop()

    def stop(self) -> None:
        self.drain()


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


def build_projection(
    database: Any, worker_id: str, *, process_role: str = "projection",
):
    _require_process_role("projection", process_role)
    from services.agent.runtime.application.confirmation_notification import (
        ToolConfirmationNotificationWorker,
    )
    from services.agent.runtime.application.projection_worker import (
        CompatibilityProjectionWorker,
    )
    from services.agent.runtime.infrastructure.postgres.compat_projection import (
        PostgresCompatibilityProjection,
    )
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


def build_runtime(
    database: Any, settings, *, production_components=None,
    process_role: str | None = None,
) -> RuntimeOwner:
    _require_process_role(
        "agent_runtime", process_role or getattr(
            settings, "agent_runtime_process_role", None,
        ) or "agent_runtime",
    )
    production_enabled = bool(getattr(
        settings, "agent_runtime_production_composition_enabled", False,
    ))
    if not production_enabled:
        raise RuntimeError("RUNTIME_PRODUCTION_COMPOSITION_DISABLED")
    if production_components is not None:
        raise RuntimeError("RUNTIME_PRODUCTION_COMPONENT_INJECTION_FORBIDDEN")
    from services.agent.runtime.production_composition import (
        build_safe_runtime_composition,
    )
    from services.agent.runtime.production_factory import (
        build_production_model_gateway_components,
    )
    worker_id = settings.agent_runtime_worker_id
    db = scoped(database, DatabaseAccessKind.AGENT_RUNTIME, worker_id)
    runtime_repository = PostgresRuntimeRepository(db)
    recovery = PostgresCoordinatorRecoveryRepository(db)
    actions = PostgresActionRepository(db)
    attempts = PostgresModelAttemptRepository(db)
    versions = build_runtime_version_registry()
    gateway = build_production_model_gateway_components(db, settings)
    safe = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=db), model_port=gateway.model,
    )
    registry = safe.registry
    action_loop = ActionLoopDriver(
        recovery_repository=recovery,
        action_repository=actions,
        authorization_repository=PostgresActionAuthorizationRepository(db),
        resolver=PostgresActionExecutorResolver(registry),
        worker_id=worker_id,
        capability_issuer=None,
    )
    model_factory = PostgresModelCallFactory(
        db, worker_id, version_registry=versions,
    )
    model_loop = ModelLoopDriver(
        runtime_repository=runtime_repository,
        attempt_repository=attempts,
        action_repository=actions,
        recovery_repository=recovery,
        model=gateway.model, call_factory=model_factory,
        reconciler=retain_unknown_model_attempt,
        gateway_dispatch_repository=gateway.repository,
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
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=db),
        model_call_factory=model_factory, model_loop=model_loop,
        action_loop=action_loop, model_port=gateway.model,
    )
    return RuntimeOwner(commands, runtime, readiness=composition.readiness)


def build_safe_runtime_components(
    database: Any, settings, *, credential_broker: object,
):
    """Assemble safe Runtime loops without starting any Runtime-owned worker."""
    if credential_broker is None:
        raise RuntimeError("CREDENTIAL_BROKER_REQUIRED")
    from services.agent.runtime.infrastructure.model.adapter import (
        ExistingProviderModelAdapter,
    )
    from services.agent.runtime.production_composition import (
        build_safe_runtime_composition,
    )
    worker_id = settings.agent_runtime_worker_id
    db = scoped(database, DatabaseAccessKind.AGENT_RUNTIME, worker_id)
    registry = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=db),
    ).registry
    action_loop = ActionLoopDriver(
        recovery_repository=PostgresCoordinatorRecoveryRepository(db),
        action_repository=PostgresActionRepository(db),
        authorization_repository=PostgresActionAuthorizationRepository(db),
        resolver=PostgresActionExecutorResolver(registry),
        worker_id=worker_id,
        capability_issuer=None,
    )
    versions = build_runtime_version_registry()
    model_factory = PostgresModelCallFactory(
        db, worker_id, version_registry=versions,
    )
    model_loop = ModelLoopDriver(
        runtime_repository=PostgresRuntimeRepository(db),
        attempt_repository=PostgresModelAttemptRepository(db),
        action_repository=PostgresActionRepository(db),
        recovery_repository=PostgresCoordinatorRecoveryRepository(db),
        model=ExistingProviderModelAdapter(db=db),
        call_factory=model_factory,
        reconciler=retain_unknown_model_attempt,
    )
    return build_safe_runtime_composition(
        resources=RuntimeReadResources(database=db),
        model_call_factory=model_factory, model_loop=model_loop,
        action_loop=action_loop,
        credential_broker=credential_broker,
    )


def build_authorization(
    database: Any, worker_id: str, *, process_role: str = "authorization",
):
    _require_process_role("authorization", process_role)
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


def build_sandbox(
    database: Any, settings, *, process_role: str = "sandbox",
):
    _require_process_role("sandbox", process_role)
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


def _require_process_role(expected: str, actual: str) -> None:
    if actual != expected:
        raise RuntimeError(
            f"RUNTIME_COMPOSITION_ROLE_MISMATCH:{expected}:{actual}",
        )
