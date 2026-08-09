"""Canonical scheduled-run toolsets derived from frozen Runtime facts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from services.agent.runtime.catalog.registry import restore_frozen_toolset


_USER_TOOLS = frozenset({
    "artifact_get", "artifact_read", "artifact_search", "evidence_get",
    "evidence_search", "get_conversation_context", "memory_get",
    "memory_search", "search_knowledge",
})
_CHANNEL_TOOLS = _USER_TOOLS | frozenset({
    "local_compare_stats", "local_platform_map_query", "local_product_identify",
    "local_product_stats", "local_shop_list", "local_stock_query",
    "local_supplier_list", "local_warehouse_list",
})


@dataclass(frozen=True, kw_only=True)
class ScheduledToolsetSnapshot:
    document: dict[str, object]
    canonical_hash_input: str
    toolset_hash: str


def canonicalize_scheduled_toolset(
    definition_document: Mapping[str, object],
    catalog_document: Mapping[str, object],
    source_toolset_document: Mapping[str, object],
    *,
    catalog_revision: str,
) -> ScheduledToolsetSnapshot:
    """Filter a frozen source while retaining Runtime's canonical fact shape."""
    scope = str(source_toolset_document.get("scope_kind", ""))
    channel = str(source_toolset_document.get("channel", ""))
    approved = _USER_TOOLS if scope == "user" else _CHANNEL_TOOLS if scope == "channel" else None
    source_tools = source_toolset_document.get("tools")
    source_names = source_toolset_document.get("tool_names")
    if approved is None or channel not in {"web", "wecom"}:
        raise ValueError("SCHEDULED_TOOLSET_SCOPE_INVALID")
    if not isinstance(source_tools, list) or not isinstance(source_names, list):
        raise ValueError("SCHEDULED_TOOLSET_SOURCE_INVALID")
    by_name = {
        str(tool.get("canonical_name")): dict(tool)
        for tool in source_tools if isinstance(tool, Mapping)
    }
    if len(by_name) != len(source_tools) or set(map(str, source_names)) != set(by_name):
        raise ValueError("SCHEDULED_TOOLSET_SOURCE_INVALID")
    if "manage_scheduled_task" not in by_name or not approved.issubset(by_name):
        raise ValueError("SCHEDULED_TOOLSET_NOT_APPROVED")
    tools = [by_name[name] for name in sorted(approved)]
    groups = sorted({str(tool.get("tool_group", "")) for tool in tools})
    if "" in groups:
        raise ValueError("SCHEDULED_TOOLSET_SOURCE_INVALID")
    facts = {
        "agent_definition_hash": str(definition_document.get("definition_hash", "")),
        "catalog_revision": catalog_revision,
        "scope_kind": scope,
        "channel": channel,
        "tools": tools,
    }
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    document: dict[str, object] = {
        "scope_kind": scope,
        "channel": channel,
        "gate_state": "enabled",
        "entitled_groups": groups,
        "tool_names": sorted(approved),
        "tools": tools,
        "toolset_hash": digest,
    }
    restored = restore_frozen_toolset(
        definition_document, catalog_document, document,
        catalog_revision=catalog_revision,
    )
    if restored.toolset_hash != digest or {
        tool.canonical_name for tool in restored.definitions
    } != approved:
        raise ValueError("SCHEDULED_TOOLSET_CANONICALIZATION_INVALID")
    return ScheduledToolsetSnapshot(
        document=document, canonical_hash_input=canonical, toolset_hash=digest,
    )


__all__ = ["ScheduledToolsetSnapshot", "canonicalize_scheduled_toolset"]
