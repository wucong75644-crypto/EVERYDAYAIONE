"""Explicit non-production Catalog builder for AR-17.3.

This module is intentionally not imported by any production composition root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.agent.runtime.catalog.types import RuntimeToolDefinition
from services.agent.runtime.catalog import RuntimeToolCatalog
from services.agent.runtime.catalog.specialist_schemas import SPECIALIST_TOOL_SCHEMAS
from services.agent.runtime.executors.specialist_registry import (
    SPECIALIST_SAFETY, SPECIALIST_TOOLS, specialist_tool_names,
)

if TYPE_CHECKING:
    from services.agent.runtime.executors.registry import ExecutorRegistry


def build_nonproduction_specialist_catalog(registry: "ExecutorRegistry") -> RuntimeToolCatalog:
    """Build only from an explicitly supplied specialist registry."""
    catalog = RuntimeToolCatalog()
    for descriptor in registry.descriptors():
        for name in sorted(descriptor.action_kinds):
            if name not in SPECIALIST_TOOLS:
                raise ValueError("NONPRODUCTION_SPECIALIST_REGISTRY_MIXED")
            schema = SPECIALIST_TOOL_SCHEMAS.get(name)
            if schema is None:
                raise ValueError(f"SPECIALIST_SCHEMA_MISSING:{name}")
            catalog.register(RuntimeToolDefinition(
                canonical_name=name, tool_group=_group(name), schema=schema,
                description=_legacy_description(name),
                provider_schema=_legacy_provider_schema(name),
                safety_level=SPECIALIST_SAFETY[name],
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
    if {tool.canonical_name for tool in catalog.definitions()} != set(specialist_tool_names()):
        raise ValueError("SPECIALIST_CATALOG_INCOMPLETE")
    return catalog


def _group(name: str) -> str:
    from services.agent.runtime.executors.specialist_registry import (
        ARTIFACT_JOB_TOOLS, CHILD_RUN_TOOLS, ERP_CATALOG_TOOLS, ERP_MUTATION_TOOLS, MEDIA_TOOLS,
        REMOTE_READ_TOOLS, SCHEDULED_TASK_TOOLS, SYNC_TOOLS,
        WORKSPACE_MUTATION_TOOLS,
    )
    for names, group in (
        (REMOTE_READ_TOOLS, "remote"), (ERP_CATALOG_TOOLS, "erp_catalog"), (ARTIFACT_JOB_TOOLS, "artifact"),
        (MEDIA_TOOLS, "media"), (CHILD_RUN_TOOLS, "composite"),
        (ERP_MUTATION_TOOLS, "erp_write"), (SYNC_TOOLS, "erp_sync"),
        (WORKSPACE_MUTATION_TOOLS, "workspace"), (SCHEDULED_TASK_TOOLS, "scheduler"),
    ):
        if name in names:
            return group
    raise ValueError(f"SPECIALIST_GROUP_MISSING:{name}")


def _legacy_description(tool_name: str) -> str:
    from services.agent.runtime.catalog.legacy_contract import (
        legacy_tool_description,
    )

    return legacy_tool_description(tool_name)


def _legacy_provider_schema(tool_name: str):
    from services.agent.runtime.catalog.legacy_contract import (
        legacy_tool_parameters,
    )

    return legacy_tool_parameters(tool_name)
