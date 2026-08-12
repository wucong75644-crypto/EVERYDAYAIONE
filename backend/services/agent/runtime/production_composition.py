"""Production composition for the complete AR-17 tool surface.

Only concrete, injected ports are accepted here.  Test adapters and the
non-production catalog module are intentionally unreachable from this root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from services.agent.runtime.catalog.production import (
    ProductionCatalogReceipt, ProductionToolBinding, build_production_catalog,
)
from services.agent.runtime.executors.family_executors import EXECUTOR_BY_FAMILY
from services.agent.runtime.executors.provider_adapters import (
    ArtifactPort, ChildRunPort, CrawlerProvider, DashScopeSearchProvider,
    ErpApiSearchProvider, ErpDispatcherFactoryPort, ErpDispatcherPort,
    ERPQueryProvider, KieMediaProvider,
    LocalArtifactProvider, MediaTaskPort, PortBackedProvider, ProviderTransport,
    ResourceMutationPort, TenantProviderResolver, TenantScopedProvider,
)
from services.agent.runtime.executors.read_registry import (
    READ_TOOL_SPECS, SAFE_READ_TOOL_NAMES, build_read_executor_registry,
)
from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.executors.real_domain import (
    ArtifactReadCapability, ConversationReadCapability, EvidenceReadCapability,
    KnowledgeReadCapability, MemoryReadCapability, WorkspaceReadCapability,
)
from services.agent.runtime.executors.real_erp import ErpLocalReadCapability
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.catalog import RuntimeToolCatalog
from services.agent.runtime.executors.specialist_registry import (
    ARTIFACT_JOB_TOOLS, CHILD_RUN_TOOLS, ERP_CATALOG_TOOLS, MEDIA_TOOLS,
    ERP_RUNTIME_READ_TOOLS, REMOTE_READ_TOOLS, SCHEDULED_TASK_TOOLS,
    SPECIALIST_FAMILIES,
    SPECIALIST_SAFETY, WORKSPACE_MUTATION_TOOLS, specialist_descriptor,
)
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.data_read_composition import (
    build_runtime_data_read_registry, data_readiness,
)
from services.agent.runtime.infrastructure.postgres.specialist_repository import (
    PostgresSpecialistRepository,
)
from services.agent.runtime.providers.callback_inbox import (
    CallbackInbox, CallbackSignatureVerifier,
)
from services.agent.runtime.production_services import ProductionServiceBundle
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.executors.resolver import PostgresActionExecutorResolver
from services.agent.runtime.infrastructure.postgres.action_repository import PostgresActionRepository
from services.agent.runtime.infrastructure.postgres.authorization import PostgresActionAuthorizationRepository
from services.agent.runtime.infrastructure.postgres.coordinator_recovery import PostgresCoordinatorRecoveryRepository
from services.agent.runtime.runtime_assembly import (
    CapabilityReadiness, CapabilityReadinessState, RuntimeAssemblyReadiness,
)
def build_production_components_for_worker(
    *, database: object, settings: object,
    sandbox_registry: ExecutorRegistry,
) -> "ProductionRuntimeComponents":
    """Delegate Worker construction to the sole code-owned production root."""
    from services.agent.runtime.production_factory import (
        build_agent_runtime_production_components,
    )

    components = build_agent_runtime_production_components(
        database, settings, sandbox_registry,
    )
    if not isinstance(components, ProductionRuntimeComponents):
        raise RuntimeError("RUNTIME_PRODUCTION_COMPONENT_FACTORY_INVALID")
    if not isinstance(components.readiness, RuntimeAssemblyReadiness):
        raise RuntimeError(
            "RUNTIME_PRODUCTION_COMPONENT_READINESS_INVALID"
        )
    return components


@dataclass(frozen=True, kw_only=True)
class ProductionSpecialistPorts:
    transport: ProviderTransport
    erp_dispatcher: ErpDispatcherPort
    erp_dispatcher_factory: ErpDispatcherFactoryPort | None = None
    erp_search: object
    artifact: ArtifactPort
    media_task: MediaTaskPort
    resource_mutation: ResourceMutationPort
    child_run: ChildRunPort
    facts: object | None = None
    local_data: ArtifactPort | None = None
    file_analyze: ArtifactPort | None = None
    fetch_all_pages: ArtifactPort | None = None
    provider_resolver: TenantProviderResolver | None = None
    provider_revisions: Mapping[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class ProductionRuntimeComponents:
    registry: ExecutorRegistry
    catalog: ProductionCatalogReceipt
    callback_inbox: CallbackInbox
    specialist_repository: PostgresSpecialistRepository
    readiness: RuntimeAssemblyReadiness
    service_bundle: ProductionServiceBundle | None


@dataclass(frozen=True, kw_only=True)
class SafeRuntimeComposition:
    """Read-only Runtime composition; provider-dependent tools are absent."""

    registry: ExecutorRegistry
    catalog: RuntimeToolCatalog
    readiness: RuntimeAssemblyReadiness
    model_call_factory: object | None = None
    model_loop: object | None = None
    action_loop: object | None = None
    model_port: object | None = None

    def require_capability(self, name: str) -> None:
        capability = self.readiness.capabilities.get(name)
        if capability is None:
            raise RuntimeError("RUNTIME_CAPABILITY_NOT_REGISTERED")
        if not capability.ready:
            raise RuntimeError(capability.error_code or "RUNTIME_CAPABILITY_NOT_READY")
def build_safe_runtime_composition(
    *, resources: RuntimeReadResources, model_call_factory: object | None = None,
    model_loop: object | None = None, action_loop: object | None = None,
    credential_broker: object | None = None, model_port: object | None = None,
    erp_dispatcher_factory: ErpDispatcherFactoryPort | None = None,
    local_data: ArtifactPort | None = None,
    file_analyze: ArtifactPort | None = None,
    fetch_all_pages: ArtifactPort | None = None,
) -> SafeRuntimeComposition:
    """Compose safe reads and explicitly supplied Runtime loop seams."""
    if resources is None or resources.database is None:
        raise RuntimeError("RUNTIME_SAFE_READ_SERVICE_WIRING_NOT_READY")
    registry = build_production_read_registry(
        resources, tool_names=SAFE_READ_TOOL_NAMES,
    )
    if erp_dispatcher_factory is not None:
        registry = _merge_registries(
            registry,
            build_runtime_erp_read_registry(erp_dispatcher_factory),
        )
    data_registry = build_runtime_data_read_registry(
        local_data=local_data, file_analyze=file_analyze,
        fetch_all_pages=fetch_all_pages,
    )
    data_ready = bool(data_registry.descriptors())
    if data_ready:
        registry = _merge_registries(registry, data_registry)
    catalog = RuntimeToolCatalog.from_executor_registry(registry)
    model_ready = (
        callable(model_call_factory) and (
            model_port is not None or (
                credential_broker is not None and _credential_ready(credential_broker)
            )
        )
    )
    action_ready = action_loop is not None
    capabilities = {
        "runtime.read": CapabilityReadiness(
            state=CapabilityReadinessState.READY,
        ),
        "runtime.erp.read": CapabilityReadiness(
            state=(CapabilityReadinessState.READY if erp_dispatcher_factory
                   is not None else CapabilityReadinessState.UNAVAILABLE),
            error_code=(None if erp_dispatcher_factory is not None
                        else "ERP_READ_CONFIGURATION_NOT_READY"),
        ),
        "runtime.model": CapabilityReadiness(
            state=(CapabilityReadinessState.READY if model_ready
                   else CapabilityReadinessState.UNAVAILABLE),
            error_code=None if model_ready else "MODEL_SNAPSHOT_WIRING_NOT_READY",
        ),
        "runtime.action": CapabilityReadiness(
            state=(CapabilityReadinessState.READY if action_ready
                   else CapabilityReadinessState.UNAVAILABLE),
            error_code=None if action_ready else "ACTION_LOOP_WIRING_NOT_READY",
        ),
        "runtime.erp.write": CapabilityReadiness(
            state=CapabilityReadinessState.DISABLED,
        ),
        "runtime.data.read": data_readiness(data_ready),
        "runtime.media": CapabilityReadiness(
            state=CapabilityReadinessState.DISABLED,
        ),
        "runtime.external_specialist": CapabilityReadiness(
            state=CapabilityReadinessState.DISABLED,
        ),
        "runtime.worker": CapabilityReadiness(
            state=CapabilityReadinessState.DISABLED,
        ),
        "runtime.projection": CapabilityReadiness(
            state=CapabilityReadinessState.DISABLED,
        ),
        "runtime.authorization": CapabilityReadiness(
            state=CapabilityReadinessState.DISABLED,
        ),
        "runtime.sandbox": CapabilityReadiness(
            state=CapabilityReadinessState.DISABLED,
        ),
    }
    readiness = RuntimeAssemblyReadiness(
        service_wiring_ready=True,
        tenant_binding_ready=True,
        credential_available=model_ready,
        capability_enabled=True,
        probe_passed=True,
        production_ready=False,
        error_code="PRODUCTION_READINESS_DISABLED",
        capabilities=capabilities,
    )
    return SafeRuntimeComposition(
        registry=registry, catalog=catalog, readiness=readiness,
        model_call_factory=model_call_factory, model_loop=model_loop,
        action_loop=action_loop, model_port=model_port,
    )


def _credential_ready(broker: object) -> bool:
    status = getattr(broker, "readiness", None)
    status = status() if callable(status) else status
    return bool(getattr(status, "ready", False))


def build_production_action_loop(*, database, worker_id: str,
                                 components: ProductionRuntimeComponents,
                                 capability_issuer) -> ActionLoopDriver:
    """Connect the production registry to the sole PolicyReceipt/Dispatch path."""
    return ActionLoopDriver(
        recovery_repository=PostgresCoordinatorRecoveryRepository(database),
        action_repository=PostgresActionRepository(database),
        authorization_repository=PostgresActionAuthorizationRepository(database),
        resolver=PostgresActionExecutorResolver(components.registry),
        worker_id=worker_id,
        capability_issuer=capability_issuer,
        specialist_facts=components.specialist_repository,
    )


def build_production_read_registry(
    resources: RuntimeReadResources, *,
    tool_names: frozenset[str] | None = None,
) -> ExecutorRegistry:
    capabilities = {
        "get_conversation_context": ConversationReadCapability(resources),
        "search_knowledge": KnowledgeReadCapability(resources),
        "evidence_search": EvidenceReadCapability(resources),
        "evidence_get": EvidenceReadCapability(resources),
        "memory_search": MemoryReadCapability(resources),
        "memory_get": MemoryReadCapability(resources),
        "artifact_search": ArtifactReadCapability(resources),
        "artifact_get": ArtifactReadCapability(resources),
        "artifact_read": ArtifactReadCapability(resources),
        "file_search": WorkspaceReadCapability(resources),
    }
    capabilities.update({
        name: ErpLocalReadCapability(resources, name)
        for name, (_, group) in READ_TOOL_SPECS.items()
        if group == "erp_local"
    })
    return build_read_executor_registry(capabilities, tool_names=tool_names)


def build_runtime_erp_read_registry(
    dispatcher_factory: ErpDispatcherFactoryPort,
) -> ExecutorRegistry:
    """Compose only governed tenant ERP reads; no write/sync/Taobao tools."""
    registry = ExecutorRegistry()
    for tool in sorted(ERP_RUNTIME_READ_TOOLS):
        descriptor = specialist_descriptor(tool)
        registry.register_read(
            descriptor,
            EXECUTOR_BY_FAMILY["remote_read"](
                action_kind=tool, executor_type=descriptor.executor_type,
                revision=descriptor.revision,
                provider=ERPQueryProvider(
                    tool_name=tool, dispatcher_factory=dispatcher_factory,
                ),
                async_submit=False,
            ),
            safety_level="safe",
        )
    return registry


def build_production_specialist_registry(
    ports: ProductionSpecialistPorts, *, facts: object,
) -> ExecutorRegistry:
    providers: dict[str, object] = {}
    for tool in REMOTE_READ_TOOLS:
        providers[tool] = (
            DashScopeSearchProvider(ports.transport) if tool == "web_search"
            else CrawlerProvider(ports.transport) if tool == "social_crawler"
            else ERPQueryProvider(
                ports.erp_dispatcher, tool_name=tool,
                dispatcher_factory=ports.erp_dispatcher_factory,
            )
        )
    for tool in ERP_CATALOG_TOOLS:
        providers[tool] = ErpApiSearchProvider(search=ports.erp_search)
    for tool in ARTIFACT_JOB_TOOLS:
        specialized = {
            "local_data": ports.local_data, "file_analyze": ports.file_analyze,
            "fetch_all_pages": ports.fetch_all_pages,
        }.get(tool)
        if specialized is None: raise RuntimeError(f"RUNTIME_SPECIALIST_SERVICE_WIRING_NOT_READY:{tool}")
        providers[tool] = LocalArtifactProvider(
            port=specialized, operation=tool,
        )
    providers["generate_image"] = KieMediaProvider(
        ports.transport, kind="image", task_port=ports.media_task,
    )
    providers["generate_video"] = KieMediaProvider(
        ports.transport, kind="video", task_port=ports.media_task,
    )
    for tool in CHILD_RUN_TOOLS:
        providers[tool] = PortBackedProvider(
            port=ports.child_run, operation=tool, provider="child_run",
        )
    providers["erp_execute"] = ERPQueryProvider(
        ports.erp_dispatcher, tool_name="erp_execute", write=True,
    )
    providers["trigger_erp_sync"] = PortBackedProvider(
        port=ports.resource_mutation, operation="trigger_erp_sync", provider="erp_sync",
    )
    for tool in WORKSPACE_MUTATION_TOOLS:
        providers[tool] = PortBackedProvider(
            port=ports.resource_mutation, operation=tool, provider="workspace",
        )
    for tool in SCHEDULED_TASK_TOOLS:
        providers[tool] = PortBackedProvider(
            port=ports.resource_mutation, operation=tool, provider="scheduler",
        )
    registry = ExecutorRegistry()
    registry.specialist_facts = facts
    if ports.provider_resolver is not None:
        revisions = ports.provider_revisions or {}
        if set(revisions) != set(SPECIALIST_FAMILIES):
            raise ValueError("RUNTIME_PROVIDER_REVISION_SET_REQUIRED")
    for tool in sorted(SPECIALIST_FAMILIES):
        descriptor = specialist_descriptor(tool)
        provider = providers[tool]
        if ports.provider_resolver is not None:
            provider = TenantScopedProvider(
                ports.provider_resolver, tool,
                expected_provider_revision=(ports.provider_revisions or {})[tool],
            )
        registry.register(
            descriptor,
            EXECUTOR_BY_FAMILY[SPECIALIST_FAMILIES[tool]](
                action_kind=tool, executor_type=descriptor.executor_type,
                revision=descriptor.revision, provider=provider,
                async_submit=descriptor.mode.value != "immediate_read",
            ),
            safety_level=SPECIALIST_SAFETY[tool],
        )
    return registry

def build_production_components(
    *, database, read_resources: RuntimeReadResources,
    specialist_ports: ProductionSpecialistPorts,
    sandbox_registry: ExecutorRegistry,
    bindings: Mapping[str, ProductionToolBinding],
    callback_verifier: CallbackSignatureVerifier,
    service_bundle: object | None = None,
) -> ProductionRuntimeComponents:
    facts = PostgresSpecialistRepository(database)
    read_registry = build_production_read_registry(read_resources)
    specialist_registry = build_production_specialist_registry(
        specialist_ports, facts=facts,
    )
    catalog = build_production_catalog(
        read_registry=read_registry, sandbox_registry=sandbox_registry,
        specialist_registry=specialist_registry, bindings=bindings,
    )
    return ProductionRuntimeComponents(
        registry=_merge_registries(read_registry, sandbox_registry, specialist_registry),
        catalog=catalog,
        callback_inbox=CallbackInbox(callback_verifier, facts),
        specialist_repository=facts,
        readiness=RuntimeAssemblyReadiness(
            service_wiring_ready=service_bundle is not None,
            tenant_binding_ready=False,
            credential_available=False,
            capability_enabled=False,
            probe_passed=False,
            production_ready=False,
            error_code="TENANT_PROVIDER_BINDING_NOT_READY",
        ),
        service_bundle=service_bundle,
    )


def build_production_components_from_services(
    *, database, read_resources: RuntimeReadResources, transport: ProviderTransport,
    erp_dispatcher: ErpDispatcherPort, erp_search: object, artifact: ArtifactPort,
    media_task: MediaTaskPort, child_run: ChildRunPort, workspace: object,
    scheduler: object, sync: object | None, callback_verifier: CallbackSignatureVerifier,
    sandbox_registry: ExecutorRegistry, bindings: Mapping[str, ProductionToolBinding],
    local_data: ArtifactPort | None = None,
    file_analyze: ArtifactPort | None = None, fetch_all_pages: ArtifactPort | None = None,
    provider_resolver: TenantProviderResolver | None = None,
    credential_broker: object | None = None,
    erp_dispatcher_factory: ErpDispatcherFactoryPort | None = None,
) -> ProductionRuntimeComponents:
    """Production service join: Artifact, Workspace, Scheduler and Sync share one facts port."""
    from dataclasses import replace
    from services.agent.runtime.executors.resource_contracts import (
        ContentAddressedArtifactService, RuntimeResourceMutationService,
        ErpSyncService, ScheduledTaskService, WorkspaceResourceService,
    )
    from services.agent.runtime.executors.resource_support import ChildRunService
    from services.agent.runtime.production_services import (
        FactBoundArtifactPort, FactBoundChildRunPort, FactBoundWorkspacePort,
    )
    facts = PostgresSpecialistRepository(database)
    if isinstance(workspace, WorkspaceResourceService) and workspace.facts is None:
        workspace = replace(workspace, facts=facts)
    if isinstance(scheduler, ScheduledTaskService) and scheduler.facts is None:
        scheduler = replace(scheduler, facts=facts)
    if isinstance(sync, ErpSyncService) and sync.facts is None:
        sync = replace(sync, facts=facts)
    if not isinstance(workspace, WorkspaceResourceService):
        raise RuntimeError("WORKSPACE_SERVICE_WIRING_NOT_READY")
    workspace = FactBoundWorkspacePort(service=workspace, facts=facts)
    if not isinstance(artifact, ContentAddressedArtifactService):
        raise RuntimeError("ARTIFACT_SERVICE_WIRING_NOT_READY")
    artifact = FactBoundArtifactPort(service=artifact, facts=facts)
    if not isinstance(child_run, ChildRunService):
        raise RuntimeError("CHILD_RUN_SERVICE_WIRING_NOT_READY")
    child_run = FactBoundChildRunPort(service=child_run, facts=facts)
    resources = RuntimeResourceMutationService(
        workspace=workspace, scheduler=scheduler, sync=sync, facts=facts,
    )
    service_bundle = None
    if provider_resolver is not None:
        from services.agent.runtime.production_services import (
            ProductionServicePorts, ReadinessResult,
            build_production_service_bundle,
        )
        service_bundle = build_production_service_bundle(
            ports=ProductionServicePorts(
                erp_dispatcher=erp_dispatcher, erp_search=erp_search,
                transport=transport, media_task=media_task, artifact=artifact,
                workspace=workspace, scheduler=scheduler, child_run=child_run,
                local_data=local_data, file_analyze=file_analyze,
                fetch_all_pages=fetch_all_pages, sync=sync,
            ),
            provider_resolver=provider_resolver,
            readiness=ReadinessResult(
                service_wiring_ready=True, tenant_binding_ready=False,
                credential_available=False, capability_enabled=False,
                probe_passed=False, error_code="PROVIDER_NOT_READY",
            ),
            credential_broker=credential_broker,
        )
    return build_production_components(
        database=database, read_resources=read_resources,
        specialist_ports=ProductionSpecialistPorts(
            transport=transport, erp_dispatcher=erp_dispatcher,
            erp_dispatcher_factory=erp_dispatcher_factory,
            erp_search=erp_search, artifact=artifact, media_task=media_task,
            resource_mutation=resources, child_run=child_run, facts=facts,
            local_data=local_data, file_analyze=file_analyze,
            fetch_all_pages=fetch_all_pages, provider_resolver=provider_resolver,
            provider_revisions={
                name: binding.provider_revision for name, binding in bindings.items()
                if name in SPECIALIST_FAMILIES
            } if provider_resolver is not None else None,
        ), sandbox_registry=sandbox_registry, bindings=bindings,
        callback_verifier=callback_verifier, service_bundle=service_bundle,
    )


def _merge_registries(*registries: ExecutorRegistry) -> ExecutorRegistry:
    result = ExecutorRegistry()
    for registry in registries:
        for descriptor in registry.descriptors():
            _, executor = registry.resolve(next(iter(descriptor.action_kinds)))
            result.register(
                descriptor, executor,
                safety_level=registry.safety_level(next(iter(descriptor.action_kinds))),
            )
    return result


__all__ = [
    "ProductionRuntimeComponents", "ProductionSpecialistPorts",
    "SafeRuntimeComposition", "build_safe_runtime_composition",
    "build_production_components", "build_production_read_registry",
    "build_runtime_erp_read_registry",
    "build_runtime_data_read_registry",
    "build_production_specialist_registry",
    "build_production_components_from_services",
    "build_production_action_loop",
    "build_production_components_for_worker",
]
