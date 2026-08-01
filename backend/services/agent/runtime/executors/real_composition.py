"""Non-production composition of every AR-17.2 real read capability."""

from __future__ import annotations

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.read_registry import (
    READ_TOOL_SPECS, build_read_executor_registry,
)
from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.executors.real_domain import (
    ArtifactReadCapability, ConversationReadCapability,
    EvidenceReadCapability, KnowledgeReadCapability, MemoryReadCapability,
    WorkspaceReadCapability,
)
from services.agent.runtime.executors.real_erp import ErpLocalReadCapability


def build_nonproduction_read_registry(
    resources: RuntimeReadResources,
) -> ExecutorRegistry:
    """Build a real registry for isolated tests; never called by production roots."""
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
    return build_read_executor_registry(capabilities)
