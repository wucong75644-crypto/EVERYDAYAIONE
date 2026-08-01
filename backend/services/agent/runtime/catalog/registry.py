"""Deterministic catalog built only from registered Executors."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Iterable, Mapping

from services.agent.runtime.agents import AgentDefinition, AgentDefinitionRegistry
from services.agent.runtime.catalog.types import RuntimeToolDefinition
if TYPE_CHECKING:
    from services.agent.runtime.executors.registry import ExecutorRegistry


class RuntimeToolCatalog:
    def __init__(self, tools: Iterable[RuntimeToolDefinition] = ()) -> None:
        self._tools: dict[str, RuntimeToolDefinition] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: RuntimeToolDefinition) -> None:
        if tool.canonical_name in self._tools:
            raise ValueError("duplicate runtime tool")
        self._tools[tool.canonical_name] = tool

    def resolve(self, name: str) -> RuntimeToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError("runtime tool is not registered") from exc

    def definitions(self) -> tuple[RuntimeToolDefinition, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    @property
    def revision(self) -> str:
        facts = [{"name": t.canonical_name, "schema_hash": t.schema_hash,
                  "revision": t.revision, "safety": t.safety_level,
                  "scope_kinds": sorted(t.allowed_scope_kinds),
                  "channels": sorted(t.allowed_channels),
                  "capabilities": sorted(t.capability_requirements)}
                 for t in self.definitions()]
        return hashlib.sha256(json.dumps(
            facts, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

    @classmethod
    def from_executor_registry(cls, registry: "ExecutorRegistry") -> "RuntimeToolCatalog":
        from config.code_tools import CODE_TOOL_SCHEMAS
        from config.tool_safety import get_safety_level
        from services.agent.runtime.executors.sandbox_job import (
            SANDBOX_EXECUTOR_TYPE,
        )
        result = cls()
        for descriptor in registry.descriptors():
            for name in sorted(descriptor.action_kinds):
                schema = CODE_TOOL_SCHEMAS.get(name)
                if schema is None:
                    continue
                result.register(RuntimeToolDefinition(
                    canonical_name=name, tool_group="code", schema={
                        "type": "object", "additionalProperties": False, **schema,
                    }, safety_level=get_safety_level(name).value,
                    executor_type=descriptor.executor_type,
                    executor_revision=descriptor.revision,
                    capability_requirements=descriptor.required_capabilities,
                    allowed_scope_kinds=frozenset({"user", "channel"}),
                    allowed_channels=frozenset({"web", "wecom"}),
                    side_effect="sandbox" if descriptor.executor_type == SANDBOX_EXECUTOR_TYPE else "unknown",
                    authorization_requirement=descriptor.authorization.value,
                    retry_semantics="reconcile_only",
                    reconcile_semantics="executor_defined",
                    cancel_semantics=descriptor.cancellation.value,
                    result_schema_revision=descriptor.result_schema_revision,
                ))
        return result


def build_default_runtime_catalog() -> RuntimeToolCatalog:
    """Construct the same code_execute-only catalog in every composition root."""
    from services.agent.runtime.executors.registry import ExecutorRegistry
    from services.agent.runtime.executors.sandbox_job import (
        SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor,
    )
    return RuntimeToolCatalog.from_executor_registry(ExecutorRegistry(
        [(SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor())],
    ))


class RuntimeToolCatalogRegistry:
    """Immutable process registry used to form ingress requests."""

    def __init__(self, catalogs: Iterable[RuntimeToolCatalog] = ()) -> None:
        self._catalogs = {catalog.revision: catalog for catalog in catalogs}

    def register(self, catalog: RuntimeToolCatalog) -> None:
        if catalog.revision in self._catalogs:
            raise ValueError("duplicate runtime catalog revision")
        self._catalogs[catalog.revision] = catalog

    def resolve(self, revision: str) -> RuntimeToolCatalog:
        try:
            return self._catalogs[revision]
        except KeyError as exc:
            raise LookupError("runtime catalog is not registered") from exc

    def revisions(self) -> tuple[str, ...]:
        return tuple(sorted(self._catalogs))


class RuntimeVersionRegistry:
    """The single process entrypoint for Agent and Catalog resolution."""

    def __init__(self, agents: AgentDefinitionRegistry,
                 catalogs: RuntimeToolCatalogRegistry,
                 agent_catalogs: dict[tuple[str, str], str] | None = None) -> None:
        self.agents = agents
        self.catalogs = catalogs
        self._agent_catalogs = dict(agent_catalogs or {})

    def resolve(self, agent_key: str, agent_revision: str, catalog_revision: str):
        return (self.agents.resolve(agent_key, agent_revision),
                self.catalogs.resolve(catalog_revision))

    def resolve_for_agent(self, agent_key: str, agent_revision: str):
        definition = self.agents.resolve(agent_key, agent_revision)
        revision = self._agent_catalogs[(agent_key, agent_revision)]
        return definition, self.catalogs.resolve(revision)


def build_runtime_version_registry() -> RuntimeVersionRegistry:
    catalog = build_default_runtime_catalog()
    probe = RuntimeToolDefinition(
        canonical_name="catalog_probe", tool_group="diagnostic",
        schema={"type": "object", "additionalProperties": False},
        safety_level="safe", executor_type="unavailable", executor_revision=1,
        capability_requirements=frozenset({"catalog_probe"}),
        side_effect="none", authorization_requirement="none",
        retry_semantics="non_retryable", reconcile_semantics="none",
        cancel_semantics="none", result_schema_revision=1,
    )
    catalog_v2 = RuntimeToolCatalog((*catalog.definitions(), probe))
    agent_v1 = AgentDefinition(
        canonical_key="everydayai-default", revision="v1",
        prompt_revision="agent-runtime-production-v1",
        requested_tool_groups=frozenset({"code"}),
        channel_restrictions=frozenset({"web", "wecom"}),
    )
    agent_v2 = AgentDefinition(
        canonical_key="everydayai-default", revision="v2",
        prompt_revision="agent-runtime-production-v2",
        requested_tool_groups=frozenset({"code", "diagnostic"}),
        channel_restrictions=frozenset({"web", "wecom"}),
    )
    return RuntimeVersionRegistry(
        AgentDefinitionRegistry((agent_v1, agent_v2)),
        RuntimeToolCatalogRegistry((catalog, catalog_v2)),
        {
            ("everydayai-default", "v1"): catalog.revision,
            ("everydayai-default", "v2"): catalog_v2.revision,
        },
    )


def restore_catalog(document: Mapping[str, object]) -> RuntimeToolCatalog:
    """Rebuild a catalog from persisted facts, independent of executors."""
    tools = []
    for raw in document.get("tools", []):
        if not isinstance(raw, Mapping):
            raise ValueError("RUNTIME_CATALOG_FACT_INVALID")
        tools.append(RuntimeToolDefinition(
            canonical_name=str(raw["canonical_name"]),
            tool_group=str(raw["tool_group"]), schema=raw["schema"],
            safety_level=str(raw["safety_level"]),
            executor_type=str(raw["executor_type"]),
            executor_revision=int(raw["executor_revision"]),
            capability_requirements=frozenset(raw.get("capability_requirements", [])),
            side_effect=str(raw["side_effect"]),
            authorization_requirement=str(raw["authorization_requirement"]),
            retry_semantics=str(raw["retry_semantics"]),
            reconcile_semantics=str(raw["reconcile_semantics"]),
            cancel_semantics=str(raw["cancel_semantics"]),
            result_schema_revision=int(raw["result_schema_revision"]),
            allowed_scope_kinds=frozenset(raw.get("allowed_scope_kinds", [])),
            allowed_channels=frozenset(raw.get("allowed_channels", [])),
            schema_hash=str(raw.get("schema_hash", "")),
        ))
    return RuntimeToolCatalog(tools)


def restore_frozen_toolset(
    definition_document: Mapping[str, object],
    catalog_document: Mapping[str, object],
    toolset_document: Mapping[str, object],
    *, catalog_revision: str | None = None,
) -> object:
    from services.agent.runtime.catalog.effective_toolset import EffectiveToolset
    from services.agent.runtime.agents import AgentDefinition

    agent = AgentDefinition(
        canonical_key=str(definition_document["canonical_key"]),
        revision=str(definition_document["revision"]),
        prompt_revision=str(definition_document["prompt_revision"]),
        requested_tool_groups=frozenset(definition_document.get("requested_tool_groups", [])),
        model_policy=definition_document.get("model_policy", {}),
        context_policy=definition_document.get("context_policy", {}),
        channel_restrictions=frozenset(definition_document.get("channel_restrictions", [])),
        definition_hash=str(definition_document.get("definition_hash", "")),
    )
    catalog = restore_catalog(catalog_document)
    if catalog_revision is not None and catalog.revision != catalog_revision:
        raise ValueError("RUNTIME_CATALOG_FACT_HASH_MISMATCH")
    names = frozenset(str(name) for name in toolset_document.get("tool_names", []))
    return EffectiveToolset.build(
        agent=agent, catalog=catalog,
        scope=str(toolset_document.get("scope_kind", "")),
        channel=str(toolset_document.get("channel", "")),
        entitled_groups=frozenset(toolset_document.get("entitled_groups", [])),
        authorized_names=names,
    )
