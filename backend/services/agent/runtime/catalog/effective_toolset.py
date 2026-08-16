"""Intersection rules for the frozen model-visible toolset."""

from __future__ import annotations

import hashlib
import json
import math
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
                and scope in tool.allowed_scope_kinds
                and channel in tool.allowed_channels)
        facts = {
            "catalog_revision": catalog.revision,
            "scope_kind": scope,
            "channel": channel,
            "tools": [tool.security_facts() for tool in tools],
        }
        digest = hashlib.sha256(json.dumps(
            facts, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return cls(definitions=tools, catalog_revision=catalog.revision,
                   toolset_hash=digest)

    def provider_tools(self) -> list[dict[str, object]]:
        return [{"type": "function", "function": {
            "name": tool.canonical_name,
            "description": tool.description,
            "parameters": dict(tool.schema),
        }} for tool in self.definitions]

    def validate_call(self, name: str, arguments: Mapping[str, object],
                      *, schema_hash: str | None = None) -> None:
        tool = next((item for item in self.definitions
                     if item.canonical_name == name), None)
        if tool is None or schema_hash not in (None, tool.schema_hash):
            raise ValueError("RUNTIME_TOOL_CALL_NOT_OFFERED")
        _validate_json_value(arguments, tool.schema, path="$" )


def _validate_json_value(
    value: object, schema: Mapping[str, object], *, path: str,
) -> None:
    if not _is_json_value(value):
        raise ValueError("RUNTIME_TOOL_CALL_SCHEMA_INVALID")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, Mapping):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        properties = schema.get("properties") or {}
        if not isinstance(properties, Mapping):
            raise ValueError("RUNTIME_TOOL_SCHEMA_INVALID")
        required = schema.get("required") or []
        if not isinstance(required, list) or any(
            not isinstance(name, str) or name not in value for name in required
        ):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        for name, child in value.items():
            child_schema = properties.get(name)
            if isinstance(child_schema, Mapping):
                _validate_json_value(child, child_schema, path=f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise ValueError("RUNTIME_TOOL_SCHEMA_INVALID")
            elif isinstance(schema.get("additionalProperties"), Mapping):
                _validate_json_value(child, schema["additionalProperties"], path=f"{path}.{name}")
    elif expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
    elif expected == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        _validate_numeric_bounds(value, schema, path)
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        _validate_numeric_bounds(value, schema, path)
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        if schema.get("uniqueItems") is True and len({
            json.dumps(item, sort_keys=True, separators=(",", ":"))
            for item in value
        }) != len(value):
            raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                _validate_json_value(item, items, path=f"{path}[{index}]")
    elif expected == "null" and value is not None:
        raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
    elif expected not in (None, "null"):
        raise ValueError("RUNTIME_TOOL_SCHEMA_INVALID")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")


def _validate_numeric_bounds(
    value: int | float, schema: Mapping[str, object], path: str,
) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, (int, float)) and value < minimum:
        raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")
    if isinstance(maximum, (int, float)) and value > maximum:
        raise ValueError(f"RUNTIME_TOOL_CALL_SCHEMA_INVALID:{path}")


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item)
                   for key, item in value.items())
    return False
