"""Production-only catalog facts and versioned receipt construction.

This module is deliberately separate from the non-production catalog helpers.
It accepts only already-composed Executor registries and refuses to create
placeholder or unavailable entries.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from services.agent.runtime.agents import AgentDefinition
from services.agent.runtime.catalog.registry import RuntimeToolCatalog
from services.agent.runtime.catalog.types import RuntimeToolDefinition
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.catalog.specialist_schemas import SPECIALIST_TOOL_SCHEMAS
from services.agent.runtime.executors.specialist_registry import (
    SPECIALIST_FAMILIES, SPECIALIST_SAFETY, SPECIALIST_TOOLS,
)


EXPECTED_PRODUCTION_TOOL_COUNT = 42


@dataclass(frozen=True, kw_only=True)
class ProductionToolBinding:
    provider_revision: str
    secret_binding: str | None = None
    readiness_hash: str = ""
    ready: bool = False

    def __post_init__(self) -> None:
        if not self.provider_revision.strip():
            raise ValueError("RUNTIME_PROVIDER_REVISION_REQUIRED")
        if self.secret_binding is not None and not self.secret_binding.strip():
            raise ValueError("RUNTIME_SECRET_BINDING_INVALID")
        if len(self.readiness_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.readiness_hash.lower()
        ):
            raise ValueError("RUNTIME_READINESS_HASH_REQUIRED")
        if not self.ready:
            raise ValueError("RUNTIME_PROVIDER_NOT_READY")


@dataclass(frozen=True, kw_only=True)
class ProductionCatalogReceipt:
    catalog: RuntimeToolCatalog
    provider_revisions: Mapping[str, str]
    binding_hash: str
    production_revision: str

    def document(self) -> dict[str, object]:
        return {
            "schema_revision": 3,
            "catalog_revision": self.production_revision,
            "catalog_hash": self.production_revision,
            "tools": [
                {
                    **tool.security_facts(),
                    "provider_revision": self.provider_revisions[tool.canonical_name],
                }
                for tool in self.catalog.definitions()
            ],
            "binding_hash": self.binding_hash,
        }


@dataclass(frozen=True, kw_only=True)
class ProductionFactSnapshot:
    """One deterministic snapshot shared by seed generation and runtime."""

    receipt: ProductionCatalogReceipt
    toolset: object
    gate_state: str
    scope_kind: str
    channel: str

    def document(self) -> dict[str, object]:
        toolset = self.toolset
        return {
            "catalog": self.receipt.document(),
            "toolset": {
                "scope_kind": self.scope_kind,
                "channel": self.channel,
                "gate_state": self.gate_state,
                "tool_names": [
                    item.canonical_name for item in toolset.definitions
                ],
                "tools": [
                    {
                        **item.security_facts(),
                        "provider_revision": self.receipt.provider_revisions[
                            item.canonical_name
                        ],
                    }
                    for item in toolset.definitions
                ],
                "toolset_hash": toolset.toolset_hash,
            },
        }


def build_production_catalog(
    *,
    read_registry: ExecutorRegistry,
    sandbox_registry: ExecutorRegistry,
    specialist_registry: ExecutorRegistry,
    bindings: Mapping[str, ProductionToolBinding],
) -> ProductionCatalogReceipt:
    """Merge the three real registries into one fail-closed 42-tool catalog."""
    catalogs = (
        RuntimeToolCatalog.from_executor_registry(read_registry),
        RuntimeToolCatalog.from_executor_registry(sandbox_registry),
        _production_specialist_catalog(specialist_registry),
    )
    merged = RuntimeToolCatalog()
    for catalog in catalogs:
        for definition in catalog.definitions():
            merged.register(definition)
    definitions = merged.definitions()
    if len(definitions) != EXPECTED_PRODUCTION_TOOL_COUNT:
        raise ValueError("RUNTIME_PRODUCTION_CATALOG_COUNT_MISMATCH")
    definition_names = {tool.canonical_name for tool in definitions}
    binding_names = set(bindings)
    if binding_names != definition_names:
        missing = sorted(definition_names - binding_names)
        extra = sorted(binding_names - definition_names)
        raise ValueError(
            "RUNTIME_PROVIDER_BINDING_SET_MISMATCH:"
            f"missing={missing}:extra={extra}"
        )
    provider_revisions: dict[str, str] = {}
    binding_facts: list[dict[str, object]] = []
    for tool in definitions:
        binding = bindings.get(tool.canonical_name)
        if binding is None:
            raise ValueError(f"RUNTIME_PROVIDER_BINDING_MISSING:{tool.canonical_name}")
        if tool.executor_type == "unavailable":
            raise ValueError("RUNTIME_UNAVAILABLE_EXECUTOR")
        if (tool.safety_level != "safe" or tool.side_effect != "none") and not binding.secret_binding:
            raise ValueError(f"RUNTIME_SECRET_BINDING_MISSING:{tool.canonical_name}")
        provider_revisions[tool.canonical_name] = binding.provider_revision
        binding_facts.append({
            "tool": tool.security_facts(),
            "provider_revision": binding.provider_revision,
            "secret_binding": binding.secret_binding,
            "readiness_hash": binding.readiness_hash,
        })
    binding_hash = hashlib.sha256(json.dumps(
        binding_facts, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    production_revision = hashlib.sha256(json.dumps({
        "catalog_revision": merged.revision,
        "bindings": binding_facts,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ProductionCatalogReceipt(
        catalog=merged, provider_revisions=provider_revisions,
        binding_hash=binding_hash, production_revision=production_revision,
    )


def build_production_toolset(
    *, agent: AgentDefinition, receipt: ProductionCatalogReceipt,
    scope: str, channel: str, entitled_groups: frozenset[str],
    authorized_names: frozenset[str], gate_state: str = "enabled",
):
    from services.agent.runtime.catalog.effective_toolset import EffectiveToolset

    if gate_state not in {"enabled", "disabled"}:
        raise ValueError("RUNTIME_TOOLSET_GATE_STATE_INVALID")
    if gate_state == "disabled":
        authorized_names = frozenset(
            name for name in authorized_names
            if receipt.catalog.resolve(name).safety_level == "safe"
            and receipt.catalog.resolve(name).side_effect == "none"
        )
    base = EffectiveToolset.build(
        agent=agent, catalog=receipt.catalog, scope=scope, channel=channel,
        entitled_groups=entitled_groups, authorized_names=authorized_names,
    )
    facts = {
        "agent_definition_hash": agent.definition_hash,
        "catalog_revision": receipt.production_revision,
        "scope_kind": scope, "channel": channel,
        "tools": [
            {
                **tool.security_facts(),
                "provider_revision": receipt.provider_revisions[tool.canonical_name],
            }
            for tool in base.definitions
        ],
    }
    toolset_hash = hashlib.sha256(json.dumps(
        facts, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return EffectiveToolset(
        definitions=base.definitions, catalog_revision=receipt.production_revision,
        toolset_hash=toolset_hash,
    )


def build_production_fact_snapshot(
    *, agent: AgentDefinition, read_registry: ExecutorRegistry,
    sandbox_registry: ExecutorRegistry, specialist_registry: ExecutorRegistry,
    bindings: Mapping[str, ProductionToolBinding], scope: str, channel: str,
    entitled_groups: frozenset[str], authorized_names: frozenset[str],
    gate_state: str,
) -> ProductionFactSnapshot:
    """Build catalog and model-visible facts from the same runtime inputs."""
    receipt = build_production_catalog(
        read_registry=read_registry, sandbox_registry=sandbox_registry,
        specialist_registry=specialist_registry, bindings=bindings,
    )
    toolset = build_production_toolset(
        agent=agent, receipt=receipt, scope=scope, channel=channel,
        entitled_groups=entitled_groups, authorized_names=authorized_names,
        gate_state=gate_state,
    )
    return ProductionFactSnapshot(
        receipt=receipt, toolset=toolset, gate_state=gate_state,
        scope_kind=scope, channel=channel,
    )


__all__ = [
    "EXPECTED_PRODUCTION_TOOL_COUNT", "ProductionCatalogReceipt",
    "ProductionFactSnapshot", "ProductionToolBinding",
    "build_production_catalog", "build_production_fact_snapshot",
    "build_production_toolset",
]


def _production_specialist_catalog(registry: ExecutorRegistry) -> RuntimeToolCatalog:
    catalog = RuntimeToolCatalog()
    for descriptor in registry.descriptors():
        for name in sorted(descriptor.action_kinds):
            if name not in SPECIALIST_TOOLS:
                raise ValueError("RUNTIME_PRODUCTION_REGISTRY_MIXED")
            schema = SPECIALIST_TOOL_SCHEMAS.get(name)
            if schema is None:
                raise ValueError(f"RUNTIME_SPECIALIST_SCHEMA_MISSING:{name}")
            family = SPECIALIST_FAMILIES[name]
            catalog.register(RuntimeToolDefinition(
                canonical_name=name, tool_group=_specialist_group(family),
                schema=schema, safety_level=SPECIALIST_SAFETY[name],
                executor_type=descriptor.executor_type,
                executor_revision=descriptor.revision,
                capability_requirements=descriptor.required_capabilities,
                allowed_scope_kinds=frozenset({"user", "channel"}),
                allowed_channels=frozenset({"web", "wecom"}),
                side_effect="external" if descriptor.mode.value != "immediate_read" else "none",
                authorization_requirement=descriptor.authorization.value,
                retry_semantics="retry_safe" if descriptor.mode.value == "immediate_read" else "reconcile_only",
                reconcile_semantics="unsupported" if descriptor.mode.value == "immediate_read" else "executor_defined",
                cancel_semantics=descriptor.cancellation.value,
                result_schema_revision=descriptor.result_schema_revision,
            ))
    if {tool.canonical_name for tool in catalog.definitions()} != set(SPECIALIST_TOOLS):
        raise ValueError("RUNTIME_SPECIALIST_CATALOG_INCOMPLETE")
    return catalog


def _specialist_group(family: str) -> str:
    return {
        "remote_read": "remote", "erp_catalog": "erp_catalog",
        "artifact_job": "artifact", "media_generation": "media",
        "child_run": "composite", "erp_mutation": "erp_write",
        "erp_sync": "erp_sync", "workspace_mutation": "workspace",
        "scheduled_task": "scheduler",
    }[family]
