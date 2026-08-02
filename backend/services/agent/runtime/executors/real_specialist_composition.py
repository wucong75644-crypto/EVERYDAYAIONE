"""Constructible, non-production composition for all 23 AR-17.3 tools."""

from __future__ import annotations

from dataclasses import dataclass

from services.agent.runtime.executors.family_executors import EXECUTOR_BY_FAMILY
from services.agent.runtime.executors.provider_adapters import (
    ArtifactPort, ChildRunPort, CrawlerProvider, DashScopeSearchProvider,
    ErpApiSearchProvider, ErpDispatcherPort, ERPQueryProvider, KieMediaProvider,
    LocalArtifactProvider, MediaTaskPort, PortBackedProvider, ProviderTransport,
    ResourceMutationPort,
)
from services.agent.runtime.infrastructure.postgres.specialist_repository import PostgresSpecialistRepository
from services.agent.runtime.providers.callback_inbox import CallbackInbox, CallbackSignatureVerifier
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.specialist_contracts import SpecialistProvider
from services.agent.runtime.executors.specialist_registry import (
    ARTIFACT_JOB_TOOLS, CHILD_RUN_TOOLS, MEDIA_TOOLS, ERP_CATALOG_TOOLS,
    REMOTE_READ_TOOLS, SCHEDULED_TASK_TOOLS, SPECIALIST_FAMILIES,
    WORKSPACE_MUTATION_TOOLS, specialist_descriptor, SPECIALIST_SAFETY,
)


@dataclass(frozen=True, kw_only=True)
class NonProductionSpecialistPorts:
    transport: ProviderTransport
    erp_dispatcher: ErpDispatcherPort
    erp_search: object
    artifact: ArtifactPort
    media_task: MediaTaskPort
    resource_mutation: ResourceMutationPort
    child_run: ChildRunPort
    facts: object | None = None
    local_data: ArtifactPort | None = None
    file_analyze: ArtifactPort | None = None
    fetch_all_pages: ArtifactPort | None = None


def build_nonproduction_specialist_registry(ports: NonProductionSpecialistPorts) -> ExecutorRegistry:
    """Build every tool with a distinct provider adapter and family Executor."""
    providers = _providers(ports)
    registry = ExecutorRegistry()
    for tool in sorted(SPECIALIST_FAMILIES):
        descriptor = specialist_descriptor(tool)
        executor_cls = EXECUTOR_BY_FAMILY[SPECIALIST_FAMILIES[tool]]
        registry.register(
            descriptor,
            executor_cls(action_kind=tool, executor_type=descriptor.executor_type,
                         revision=descriptor.revision, provider=providers[tool],
                         async_submit=descriptor.mode.value != "immediate_read",
                         facts=ports.facts),
            safety_level=SPECIALIST_SAFETY[tool],
        )
    return registry


def build_nonproduction_specialist_registry_from_services(
    *, transport: ProviderTransport, erp_dispatcher: ErpDispatcherPort,
    erp_search: object, artifact: ArtifactPort, media_task: MediaTaskPort,
    child_run: ChildRunPort, workspace: object, scheduler: object,
    sync: object | None = None, repository: PostgresSpecialistRepository | None = None,
    local_data: ArtifactPort | None = None, file_analyze: ArtifactPort | None = None,
    fetch_all_pages: ArtifactPort | None = None,
) -> ExecutorRegistry:
    """Composition root for concrete service instances, not test-only ports."""
    from dataclasses import replace
    from services.agent.runtime.executors.resource_contracts import (
        RuntimeResourceMutationService, ScheduledTaskService, WorkspaceResourceService,
    )
    if repository is not None and isinstance(workspace, WorkspaceResourceService) and workspace.facts is None:
        workspace = replace(workspace, facts=repository)
    if repository is not None and isinstance(scheduler, ScheduledTaskService) and scheduler.facts is None:
        scheduler = replace(scheduler, facts=repository)
    resources = RuntimeResourceMutationService(workspace=workspace, scheduler=scheduler, sync=sync, facts=repository)
    return build_nonproduction_specialist_registry(NonProductionSpecialistPorts(
        transport=transport, erp_dispatcher=erp_dispatcher, erp_search=erp_search,
        artifact=artifact, media_task=media_task, resource_mutation=resources,
        child_run=child_run, facts=repository, local_data=local_data,
        file_analyze=file_analyze, fetch_all_pages=fetch_all_pages,
    ))


def build_nonproduction_specialist_runtime(
    *, callback_verifier: CallbackSignatureVerifier, repository: PostgresSpecialistRepository,
    transport: ProviderTransport, erp_dispatcher: ErpDispatcherPort, erp_search: object,
    artifact: ArtifactPort, media_task: MediaTaskPort, child_run: ChildRunPort,
    workspace: object, scheduler: object, sync: object | None = None,
    local_data: ArtifactPort | None = None, file_analyze: ArtifactPort | None = None,
    fetch_all_pages: ArtifactPort | None = None,
) -> tuple[ExecutorRegistry, CallbackInbox]:
    """Composition root that exposes both Executors and durable callback ingress."""
    registry = build_nonproduction_specialist_registry_from_services(
        transport=transport, erp_dispatcher=erp_dispatcher, erp_search=erp_search,
        artifact=artifact, media_task=media_task, child_run=child_run,
        workspace=workspace, scheduler=scheduler, sync=sync, repository=repository,
        local_data=local_data, file_analyze=file_analyze, fetch_all_pages=fetch_all_pages,
    )
    return registry, CallbackInbox(callback_verifier, repository)


def _providers(ports: NonProductionSpecialistPorts) -> dict[str, SpecialistProvider]:
    providers: dict[str, SpecialistProvider] = {}
    for tool in REMOTE_READ_TOOLS:
        if tool == "web_search":
            providers[tool] = DashScopeSearchProvider(ports.transport)
        elif tool == "social_crawler":
            providers[tool] = CrawlerProvider(ports.transport)
        else:
            providers[tool] = ERPQueryProvider(ports.erp_dispatcher, tool_name=tool)
    for tool in ERP_CATALOG_TOOLS:
        providers[tool] = ErpApiSearchProvider(search=ports.erp_search)
    for tool in ARTIFACT_JOB_TOOLS:
        specialized = {"local_data": ports.local_data, "file_analyze": ports.file_analyze, "fetch_all_pages": ports.fetch_all_pages}.get(tool)
        providers[tool] = LocalArtifactProvider(port=specialized or ports.artifact, operation=tool)
    providers["generate_image"] = KieMediaProvider(ports.transport, kind="image", task_port=ports.media_task)
    providers["generate_video"] = KieMediaProvider(ports.transport, kind="video", task_port=ports.media_task)
    for tool in CHILD_RUN_TOOLS:
        providers[tool] = PortBackedProvider(port=ports.child_run, operation=tool, provider="child_run")
    providers["erp_execute"] = ERPQueryProvider(ports.erp_dispatcher, tool_name="erp_execute", write=True)
    providers["trigger_erp_sync"] = PortBackedProvider(port=ports.resource_mutation, operation="trigger_erp_sync", provider="erp_sync")
    for tool in WORKSPACE_MUTATION_TOOLS:
        providers[tool] = PortBackedProvider(port=ports.resource_mutation, operation=tool, provider="workspace")
    for tool in SCHEDULED_TASK_TOOLS:
        providers[tool] = PortBackedProvider(port=ports.resource_mutation, operation=tool, provider="scheduler")
    return providers


__all__ = ["NonProductionSpecialistPorts", "build_nonproduction_specialist_registry", "build_nonproduction_specialist_registry_from_services", "build_nonproduction_specialist_runtime"]
