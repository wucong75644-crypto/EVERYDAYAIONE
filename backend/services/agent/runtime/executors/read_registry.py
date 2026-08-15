"""AR-17.2 read Executor descriptors and test-only composition factory."""

from __future__ import annotations

from typing import Mapping

from services.agent.runtime.executors.read_only import ReadCapability, ReadOnlyExecutor
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.types import (
    AuthorizationRequirement, CancellationSupport, ExecutionMode,
    ExecutorDescriptor, IdempotencySupport,
)


READ_TOOL_SPECS: dict[str, tuple[str, str]] = {
    "get_conversation_context": ("runtime.conversation.read", "runtime"),
    "search_knowledge": ("knowledge.search", "knowledge"),
    "evidence_search": ("evidence.search", "evidence"),
    "evidence_get": ("evidence.get", "evidence"),
    "memory_search": ("memory.search", "memory"),
    "memory_get": ("memory.get", "memory"),
    "artifact_search": ("artifact.search", "artifact"),
    "artifact_get": ("artifact.get", "artifact"),
    "artifact_read": ("artifact.read", "artifact"),
    "file_search": ("workspace.file.search", "workspace"),
    "local_product_identify": ("erp.local.product_identify", "erp_local"),
    "local_stock_query": ("erp.local.stock_query", "erp_local"),
    "local_product_stats": ("erp.local.product_stats", "erp_local"),
    "local_platform_map_query": ("erp.local.platform_map_query", "erp_local"),
    "local_compare_stats": ("erp.local.compare_stats", "erp_local"),
    "local_shop_list": ("erp.local.shop_list", "erp_local"),
    "local_warehouse_list": ("erp.local.warehouse_list", "erp_local"),
    "local_supplier_list": ("erp.local.supplier_list", "erp_local"),
}
READ_SCOPE_KINDS = {
    name: frozenset({"channel"}) if group == "erp_local"
    else frozenset({"user", "channel"})
    for name, (_, group) in READ_TOOL_SPECS.items()
}

READ_EXECUTOR_REVISION = 1


def read_descriptor(tool_name: str) -> ExecutorDescriptor:
    try:
        capability, _ = READ_TOOL_SPECS[tool_name]
    except KeyError as exc:
        raise LookupError("read tool is not registered") from exc
    return ExecutorDescriptor(
        executor_type=f"runtime_read:{tool_name}",
        revision=READ_EXECUTOR_REVISION,
        action_kinds=frozenset({tool_name}), mode=ExecutionMode.IMMEDIATE_READ,
        authorization=AuthorizationRequirement.NONE,
        required_capabilities=frozenset({capability}), max_inline_ms=800,
        prepare_timeout_ms=100, submit_timeout_ms=800,
        execution_timeout_ms=5_000, reconcile_timeout_ms=100,
        idempotency=IdempotencySupport.NATIVE,
        cancellation=CancellationSupport.UNSUPPORTED, query_status=False,
        progress=False, callback=False, result_schema_revision=1,
    )


def build_read_executor_registry(
    capabilities: Mapping[str, ReadCapability],
) -> ExecutorRegistry:
    """Build an isolated registry; production composition does not call this."""
    registry = ExecutorRegistry()
    for tool_name in sorted(READ_TOOL_SPECS):
        descriptor = read_descriptor(tool_name)
        capability = capabilities.get(tool_name)
        if capability is None:
            raise ValueError(f"READ_CAPABILITY_MISSING:{tool_name}")
        registry.register_read(
            descriptor,
            ReadOnlyExecutor(
                executor_type=descriptor.executor_type,
                executor_revision=descriptor.revision,
                capability=capability,
                allowed_scope_kinds=READ_SCOPE_KINDS[tool_name],
            ),
            safety_level="safe",
        )
    return registry
