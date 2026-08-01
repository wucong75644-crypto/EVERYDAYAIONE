"""Intersection rules for the frozen model-visible toolset."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from services.agent.runtime.agents.definition import AgentDefinition
from services.agent.runtime.catalog.registry import RuntimeToolCatalog


@dataclass(frozen=True, kw_only=True)
class EffectiveToolset:
    definitions: tuple[object, ...]
    catalog_revision: str
    toolset_hash: str

    @classmethod
    def build(
        cls, *, agent: AgentDefinition, catalog: RuntimeToolCatalog,
        scope: str, channel: str, entitled_groups: frozenset[str],
        authorized_names: frozenset[str],
    ) -> "EffectiveToolset":
        if agent.channel_restrictions and channel not in agent.channel_restrictions:
            tools: tuple[object, ...] = ()
        else:
            tools = tuple(tool for tool in catalog.definitions()
                if tool.tool_group in agent.requested_tool_groups
                and tool.tool_group in entitled_groups
                and tool.canonical_name in authorized_names
                and scope)
        facts = [{"name": tool.canonical_name, "schema_hash": tool.schema_hash,
                  "revision": tool.revision} for tool in tools]
        digest = hashlib.sha256(json.dumps(
            facts, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return cls(definitions=tools, catalog_revision=catalog.revision,
                   toolset_hash=digest)

    def provider_tools(self) -> list[dict[str, object]]:
        return [{"type": "function", "function": {
            "name": tool.canonical_name,
            "parameters": dict(tool.schema),
        }} for tool in self.definitions]

    def validate_call(self, name: str, arguments: Mapping[str, object],
                      *, schema_hash: str | None = None) -> None:
        tool = next((item for item in self.definitions
                     if item.canonical_name == name), None)
        if tool is None or schema_hash not in (None, tool.schema_hash):
            raise ValueError("RUNTIME_TOOL_CALL_NOT_OFFERED")
        required = tool.schema.get("required", [])
        if any(field not in arguments for field in required):
            raise ValueError("RUNTIME_TOOL_CALL_SCHEMA_INVALID")
