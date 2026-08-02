"""Constructible, non-production composition for all 23 AR-17.3 tools."""

from __future__ import annotations

from dataclasses import dataclass

from services.agent.runtime.executors.family_executors import EXECUTOR_BY_FAMILY
from services.agent.runtime.executors.provider_adapters import (
    ArtifactPort, ChildRunPort, CrawlerProvider, DashScopeSearchProvider,
    ERPQueryProvider, KieMediaProvider, LocalArtifactProvider, MediaTaskPort,
    PortBackedProvider, ProviderTransport, ResourceMutationPort,
)
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.specialist_contracts import SpecialistProvider
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.specialist_registry import (
    ARTIFACT_JOB_TOOLS, CHILD_RUN_TOOLS, ERP_MUTATION_TOOLS, MEDIA_TOOLS,
    REMOTE_READ_TOOLS, SCHEDULED_TASK_TOOLS, SPECIALIST_FAMILIES, SYNC_TOOLS,
    WORKSPACE_MUTATION_TOOLS, specialist_descriptor, SPECIALIST_SAFETY,
)


@dataclass(frozen=True, kw_only=True)
class NonProductionSpecialistPorts:
    transport: ProviderTransport
    artifact: ArtifactPort
    media_task: MediaTaskPort
    resource_mutation: ResourceMutationPort
    child_run: ChildRunPort


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
                         async_submit=descriptor.mode.value != "immediate_read"),
            safety_level=SPECIALIST_SAFETY[tool],
        )
    return registry


def _providers(ports: NonProductionSpecialistPorts) -> dict[str, SpecialistProvider]:
    providers: dict[str, SpecialistProvider] = {}
    for tool in REMOTE_READ_TOOLS:
        if tool == "web_search":
            providers[tool] = DashScopeSearchProvider(ports.transport)
        elif tool == "social_crawler":
            providers[tool] = CrawlerProvider(ports.transport)
        else:
            providers[tool] = ERPQueryProvider(ports.transport, operation=tool.removeprefix("erp_").removesuffix("_query"))
    for tool in ARTIFACT_JOB_TOOLS:
        providers[tool] = LocalArtifactProvider(port=ports.artifact, operation=tool)
    providers["generate_image"] = KieMediaProvider(ports.transport, kind="image")
    providers["generate_video"] = KieMediaProvider(ports.transport, kind="video")
    for tool in CHILD_RUN_TOOLS:
        providers[tool] = PortBackedProvider(port=ports.child_run, operation=tool, provider="child_run")
    providers["erp_execute"] = PortBackedProvider(port=ports.resource_mutation, operation="erp_execute", provider="erp")
    providers["trigger_erp_sync"] = PortBackedProvider(port=ports.resource_mutation, operation="trigger_erp_sync", provider="erp_sync")
    for tool in WORKSPACE_MUTATION_TOOLS:
        providers[tool] = PortBackedProvider(port=ports.resource_mutation, operation=tool, provider="workspace")
    for tool in SCHEDULED_TASK_TOOLS:
        providers[tool] = PortBackedProvider(port=ports.resource_mutation, operation=tool, provider="scheduler")
    return providers


__all__ = ["NonProductionSpecialistPorts", "build_nonproduction_specialist_registry"]
