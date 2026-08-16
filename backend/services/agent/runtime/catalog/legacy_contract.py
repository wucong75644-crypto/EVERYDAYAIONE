"""Transitional metadata lookup for the legacy Chat tool surface."""

from __future__ import annotations

from functools import lru_cache
from typing import Any


# This first batch contains tools whose legacy argument names are identical to
# the current Runtime executor contract.  Semantic adapters for renamed or
# expanded arguments are intentionally handled in later batches.
LEGACY_SCHEMA_COMPATIBLE_TOOLS = frozenset({
    "artifact_get", "artifact_read", "artifact_search",
    "code_execute", "evidence_get", "evidence_search",
    "erp_api_search", "file_search", "get_conversation_context",
    "local_product_identify", "memory_get", "memory_search",
    "search_knowledge", "web_search",
})


@lru_cache(maxsize=1)
def legacy_tool_definitions() -> dict[str, dict[str, Any]]:
    """Return the legacy model-facing definitions indexed by tool name."""
    definitions: dict[str, dict[str, Any]] = {}
    from config.artifact_tools import build_artifact_tools
    from config.chat_tools import get_chat_tools
    from config.erp_tools import build_fetch_all_pages_tool
    from config.evidence_tools import build_evidence_tools
    from config.memory_tools import build_memory_tools

    sources = (
        get_chat_tools("__runtime_catalog__"),
        build_artifact_tools(), build_evidence_tools(), build_memory_tools(),
        [build_fetch_all_pages_tool()],
    )
    for tools in sources:
        for item in tools:
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            if not name:
                continue
            if name in definitions and definitions[name] != function:
                raise ValueError(f"RUNTIME_LEGACY_TOOL_DEFINITION_CONFLICT:{name}")
            definitions[name] = dict(function)

    from config.tool_registry import TOOL_REGISTRY

    for name, entry in TOOL_REGISTRY.items():
        if name not in definitions:
            definitions[name] = {"name": name, "description": entry.description}
    return definitions


def legacy_tool_description(tool_name: str) -> str:
    """Return a stable legacy description, or empty for test-only tools."""
    definition = legacy_tool_definitions().get(tool_name)
    return str(definition.get("description") or "") if definition else ""


def legacy_tool_parameters(tool_name: str) -> dict[str, Any] | None:
    """Return the exact legacy parameters for the compatible first batch."""
    if tool_name not in LEGACY_SCHEMA_COMPATIBLE_TOOLS:
        return None
    definition = legacy_tool_definitions().get(tool_name)
    parameters = definition.get("parameters") if definition else None
    return dict(parameters) if isinstance(parameters, dict) else None


__all__ = [
    "LEGACY_SCHEMA_COMPATIBLE_TOOLS", "legacy_tool_definitions",
    "legacy_tool_description", "legacy_tool_parameters",
]
