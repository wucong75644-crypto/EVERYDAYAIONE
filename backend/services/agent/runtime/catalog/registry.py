"""Deterministic catalog built only from registered Executors."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Iterable

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
                  "revision": t.revision, "safety": t.safety_level}
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
