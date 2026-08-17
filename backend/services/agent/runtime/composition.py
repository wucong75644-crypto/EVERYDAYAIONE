"""Process-exclusive production composition roots."""

from __future__ import annotations

import asyncio
import logging
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
from services.agent.runtime.infrastructure.postgres.scheduled_finalization_repository import (
    PostgresScheduledFinalizationRepository,
)
from services.agent.runtime.infrastructure.postgres.specialist_repository import (
    PostgresSpecialistRepository,
)
from services.agent.runtime.policy.evaluator import PolicyEvaluator
from services.agent.runtime.sandbox.composition import build_sandbox_worker_components
from services.agent.runtime.sandbox.nsjail import (
    NsJailSubprocessLauncher, SandboxWorkerIdentity,
)
from services.agent.runtime.registry_merge import merge_runtime_registries
from services.agent.runtime.production_model import (
    PostgresModelCallFactory, retain_unknown_model_attempt,
)
from services.agent.runtime.catalog import RuntimeToolCatalog
from services.agent.runtime.catalog.registry import build_runtime_version_registry
from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.application.scheduled_finalizer import (
    ScheduledRuntimeFinalizer,
)
from services.agent.runtime.infrastructure.stream_composition import build_runtime_stream_components


logger = logging.getLogger(__name__)

_PRODUCTION_SAFE_REQUIRED_CAPABILITIES = frozenset({
    "runtime.read",
    "runtime.erp.read",
    "runtime.model",
    "runtime.action",
    "runtime.data.read",
})


class RuntimeOwner:
    def __init__(
        self, commands, runtime, *, finalizer=None, readiness=None, stream_publisher=None,
    ) -> None:
        self.commands = commands
        self.runtime = runtime
        self.finalizer = finalizer
        self.readiness = readiness
        self._stream_publisher = stream_publisher
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
        worked = (await self.runtime.child_cancel_once()) or worked
        if self._draining:
            return worked
        worked = (await self.runtime.reconciliation_once()) or worked
        if self._draining or self.finalizer is None:
            return worked
        try:
            return (await self.finalizer.run_once()) or worked
        except Exception as exc:
            logger.error(
                "Runtime scheduled finalization failed | error_type=%s",
                type(exc).__name__,
            )
            return worked

    def drain(self) -> None:
        """Stop future claims while allowing the current fenced call to finish."""
        if self._draining:
            return
        self._draining = True
        self.commands.stop()
        self.runtime.stop()
        if self._stream_publisher is not None:
            asyncio.create_task(self._stream_publisher.close())

    def stop(self) -> None:
        self.drain()


class ProjectionOwner:
    def __init__(
        self, projection, confirmations, scheduled_delivery=None,
        media_projection=None,
    ) -> None:
        self.projection = projection
        self.confirmations = confirmations
        self.scheduled_delivery = scheduled_delivery
        self.media_projection = media_projection
        self._media_ready = False
        self._draining = False

    async def run_once(self) -> bool:
        if self._draining:
            return False
        media_projected = 0
        if self.media_projection is not None:
            media_projected = await self.media_projection.run_once()
        projected = await self.projection.run_once()
        delivered = False
        if self.scheduled_delivery is not None:
            try:
                delivered = await self.scheduled_delivery.run_once()
            except Exception as exc:
                logger.error(
                    "Runtime scheduled Web projection failed | error_type=%s",
                    type(exc).__name__,
                )
        notified = await self.confirmations.run_once()
        return bool(media_projected or projected or delivered or notified)

    def drain(self) -> None:
        self._media_ready = False
        self._draining = True

    def set_media_readiness(self, ready: bool) -> None:
        self._media_ready = bool(ready) and not self._draining

    def stop(self) -> None:
        self.drain()


def scoped(database: Any, kind: DatabaseAccessKind, worker_id: str):
    return AsyncScopedDatabaseClient(database, DatabaseScope(
        actor_user_id=None, org_id=None, access_kind=kind,
        request_id=worker_id[:128],
    ))


def build_projection(
    database: Any, worker_id: str, *, process_role: str = "projection",
    scheduled_web_projection_enabled: bool = False,
    media_projection_enabled: bool = False,
    media_workspace_root: str | None = None,
    media_cdn_domain: str | None = None,
    media_result_allowed_hosts: tuple[str, ...] = (),
):
    _require_process_role("projection", process_role)
    from services.agent.runtime.application.confirmation_notification import (
        ToolConfirmationNotificationWorker,
    )
    from services.agent.runtime.application.projection_worker import (
        CompatibilityProjectionNotifier, CompatibilityProjectionWorker,
    )
    from services.agent.runtime.infrastructure.postgres.compat_projection import (
        PostgresCompatibilityProjection,
    )
    from services.tool_confirmation import tool_confirmation_service
    from services.websocket_manager import ws_manager

    db = scoped(database, DatabaseAccessKind.PROJECTION, worker_id)
    media_projection = None
    if media_projection_enabled:
        if (
            not media_workspace_root or not media_cdn_domain
            or not media_result_allowed_hosts
        ):
            raise RuntimeError("RUNTIME_MEDIA_PROJECTION_STORAGE_REQUIRED")
        from services.agent.runtime.application.media_persistence import (
            RuntimeMediaAssetRegistry, build_runtime_media_persistence,
        )
        from services.agent.runtime.application.media_projection_worker import (
            build_runtime_media_projection_worker,
        )
        media_projection = build_runtime_media_projection_worker(
            db,
            build_runtime_media_persistence(
                asset_registry=RuntimeMediaAssetRegistry(db),
                workspace_root=media_workspace_root,
                cdn_domain=media_cdn_domain,
                allowed_result_hosts=media_result_allowed_hosts,
            ),
            ws_manager,
        )
    scheduled_delivery = None
    if scheduled_web_projection_enabled:
        from services.agent.runtime.application.scheduled_delivery_projection import (
            ScheduledDeliveryProjectionWorker,
        )
        from services.agent.runtime.infrastructure.postgres.scheduled_delivery_projection import (
            PostgresScheduledDeliveryProjection,
        )
        scheduled_delivery = ScheduledDeliveryProjectionWorker(
            PostgresScheduledDeliveryProjection(db, worker_id), ws_manager,
        )
    return ProjectionOwner(
        CompatibilityProjectionWorker(
            PostgresCompatibilityProjection(db),
            CompatibilityProjectionNotifier(db, ws_manager),
        ),
        ToolConfirmationNotificationWorker(
            database=db, service=tool_confirmation_service,
            websocket_manager=ws_manager, worker_id=worker_id,
        ),
        scheduled_delivery,
        media_projection,
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
    from services.agent.runtime.infrastructure.model.adapter import (
        ExistingProviderModelAdapter,
    )
    from services.agent.runtime.infrastructure.model.configured_adapter import (
        build_runtime_configured_adapter_factory,
    )
    from services.configuration.bundles import AsyncSecretBundleResolver
    from services.configuration.envelope import LocalKEKProvider
    from services.configuration.material_service import SecretMaterialService
    from services.agent.runtime.executors.erp_factory import (
        OrgScopedErpDispatcherFactory,
    )
    from services.agent.runtime.data_read_composition import build_runtime_data_adapters
    worker_id = settings.agent_runtime_worker_id
    db = scoped(database, DatabaseAccessKind.AGENT_RUNTIME, worker_id)
    runtime_repository = PostgresRuntimeRepository(db)
    recovery = PostgresCoordinatorRecoveryRepository(db)
    actions = PostgresActionRepository(db)
    attempts = PostgresModelAttemptRepository(db)
    specialist_facts = PostgresSpecialistRepository(db)
    versions = build_runtime_version_registry()
    try:
        material_service = SecretMaterialService(
            LocalKEKProvider.from_environment(),
        )
    except ValueError:
        raise RuntimeError("RUNTIME_MODEL_CONFIGURATION_NOT_READY") from None
    bundle_resolver = AsyncSecretBundleResolver(db, material_service)
    configured_factory = build_runtime_configured_adapter_factory(bundle_resolver)
    model = ExistingProviderModelAdapter(
        request_adapter_factory=configured_factory,
    )
    stream_publisher, stream_observer_builder = build_runtime_stream_components(settings, worker_id=worker_id)
    erp_factory = OrgScopedErpDispatcherFactory(
        db, worker_id=worker_id, material_service=material_service,
    )
    data_adapters = build_runtime_data_adapters(
        db, worker_id=worker_id, erp_dispatcher_factory=erp_factory,
    )
    safe = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=db), model_port=model,
        erp_dispatcher_factory=erp_factory,
        **data_adapters,
    )
    media = _build_runtime_media(
        database=db, settings=settings, bundle_resolver=bundle_resolver,
        specialist_facts=specialist_facts,
    )
    registry = merge_runtime_registries(safe.registry, media.registry)
    action_loop = ActionLoopDriver(
        recovery_repository=recovery,
        action_repository=actions,
        authorization_repository=PostgresActionAuthorizationRepository(db),
        resolver=PostgresActionExecutorResolver(registry),
        worker_id=worker_id,
        capability_issuer=None,
        specialist_facts=specialist_facts,
    )
    model_factory = PostgresModelCallFactory(
        db, worker_id, version_registry=versions,
        executor_registry=registry,
        stream_observer_builder=stream_observer_builder,
    )
    model_loop = ModelLoopDriver(
        runtime_repository=runtime_repository,
        attempt_repository=attempts,
        action_repository=actions,
        recovery_repository=recovery,
        model=model, call_factory=model_factory,
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
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=db),
        model_call_factory=model_factory, model_loop=model_loop,
        action_loop=action_loop, model_port=model,
        erp_dispatcher_factory=erp_factory,
        production_ready=True,
        required_capabilities=_PRODUCTION_SAFE_REQUIRED_CAPABILITIES,
        **data_adapters,
    )
    finalizer = ScheduledRuntimeFinalizer(
        PostgresScheduledFinalizationRepository(db), worker_id,
    )
    return RuntimeOwner(commands, runtime, finalizer=finalizer, readiness=composition.readiness, stream_publisher=stream_publisher)


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


def _build_runtime_media(*, database: Any, settings: Any,
                         bundle_resolver: object,
                         specialist_facts: object):
    from services.agent.runtime.media_composition import build_runtime_media_composition
    from services.agent.runtime.providers.kie_credentials import PostgresRuntimeKieCredentialSource
    from services.agent.runtime.providers.kie_transport import HttpxKieOneShotTransport
    from services.agent.runtime.providers.legacy_kie_adapter import RuntimeLegacyKieAdapterFactory
    enabled = bool(getattr(settings, "agent_runtime_media_enabled", False))
    return build_runtime_media_composition(
        database=database,
        transport=HttpxKieOneShotTransport() if enabled else None,
        credentials=PostgresRuntimeKieCredentialSource(bundle_resolver) if enabled else None,
        specialist_facts=specialist_facts, enabled=enabled,
        provider_probe_passed=bool(getattr(
            settings, "agent_runtime_media_provider_probe_passed", False,
        )),
        production_ready=bool(getattr(
            settings, "agent_runtime_media_production_ready", False,
        )),
        tool_names=frozenset({"generate_image"}),
        legacy_adapter_factory=RuntimeLegacyKieAdapterFactory(),
    )
