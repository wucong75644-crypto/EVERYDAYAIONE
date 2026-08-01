"""AR-17.3 specialist descriptors and isolated composition."""

from __future__ import annotations

from typing import Mapping

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.specialist_contracts import SpecialistProvider
from services.agent.runtime.executors.types import (
    AuthorizationRequirement, CancellationSupport, ExecutionMode,
    ExecutorDescriptor, IdempotencySupport,
)


REMOTE_READ_TOOLS = frozenset({
    "erp_product_query", "erp_trade_query", "erp_purchase_query",
    "erp_aftersales_query", "erp_warehouse_query", "erp_info_query",
    "erp_taobao_query", "erp_api_search", "web_search", "social_crawler",
})
ARTIFACT_JOB_TOOLS = frozenset({"local_data", "file_analyze", "fetch_all_pages"})
MEDIA_TOOLS = frozenset({"generate_image", "generate_video"})
CHILD_RUN_TOOLS = frozenset({"image_agent", "erp_agent", "erp_analyze"})
ERP_MUTATION_TOOLS = frozenset({"erp_execute"})
SYNC_TOOLS = frozenset({"trigger_erp_sync"})
WORKSPACE_MUTATION_TOOLS = frozenset({"file_delete", "restore_file"})
SCHEDULED_TASK_TOOLS = frozenset({"manage_scheduled_task"})

SPECIALIST_TOOLS = frozenset().union(
    REMOTE_READ_TOOLS, ARTIFACT_JOB_TOOLS, MEDIA_TOOLS, CHILD_RUN_TOOLS,
    ERP_MUTATION_TOOLS, SYNC_TOOLS, WORKSPACE_MUTATION_TOOLS,
    SCHEDULED_TASK_TOOLS,
)

SPECIALIST_FAMILIES = {
    **{tool: "remote_read" for tool in REMOTE_READ_TOOLS},
    **{tool: "artifact_job" for tool in ARTIFACT_JOB_TOOLS},
    **{tool: "media_generation" for tool in MEDIA_TOOLS},
    **{tool: "child_run" for tool in CHILD_RUN_TOOLS},
    "erp_execute": "erp_mutation", "trigger_erp_sync": "erp_sync",
    **{tool: "workspace_mutation" for tool in WORKSPACE_MUTATION_TOOLS},
    "manage_scheduled_task": "scheduled_task",
}
SPECIALIST_EXECUTOR_TYPES = {
    tool: f"runtime_{family}:{tool}" for tool, family in SPECIALIST_FAMILIES.items()
}

SPECIALIST_SAFETY = {
    **{tool: "safe" for tool in REMOTE_READ_TOOLS if tool != "web_search"},
    "web_search": "confirm", "local_data": "safe",
    "file_analyze": "confirm", "fetch_all_pages": "confirm",
    **{tool: "confirm" for tool in MEDIA_TOOLS | CHILD_RUN_TOOLS},
    "erp_execute": "dangerous", "trigger_erp_sync": "dangerous",
    **{tool: "dangerous" for tool in WORKSPACE_MUTATION_TOOLS},
    "manage_scheduled_task": "dangerous",
}

_GROUP_CAPABILITY = {
    "remote_read": "network.provider.read", "artifact_job": "artifact.materialize",
    "media_generation": "media.provider.submit", "child_run": "runtime.child_run.create",
    "erp_mutation": "network.provider.write", "erp_sync": "erp.sync.submit",
    "workspace_mutation": "workspace.resource.mutate", "scheduled_task": "scheduler.task.cas",
}


def specialist_descriptor(tool: str) -> ExecutorDescriptor:
    """Return the single descriptor for a frozen AR-17.3 tool."""
    executor_type = SPECIALIST_EXECUTOR_TYPES[tool]
    family = SPECIALIST_FAMILIES[tool]
    mode = (
        ExecutionMode.IMMEDIATE_READ if tool in REMOTE_READ_TOOLS else
        ExecutionMode.ASYNC_GENERATION if tool in MEDIA_TOOLS else
        ExecutionMode.CHILD_RUN if tool in CHILD_RUN_TOOLS else
        ExecutionMode.EXTERNAL_ACTION if tool in ERP_MUTATION_TOOLS else
        ExecutionMode.RESOURCE_MUTATION if tool in WORKSPACE_MUTATION_TOOLS else
        ExecutionMode.REMOTE_EXTENSION if tool in {"trigger_erp_sync", "manage_scheduled_task"}
        else ExecutionMode.LOCAL_RENDER
    )
    auth = (AuthorizationRequirement.NONE if tool in REMOTE_READ_TOOLS
            else AuthorizationRequirement.EXPLICIT_INTENT)
    return ExecutorDescriptor(
        executor_type=executor_type, revision=1,
        action_kinds=frozenset({tool}), mode=mode, authorization=auth,
        required_capabilities=frozenset({_GROUP_CAPABILITY[family]}),
        max_inline_ms=800 if mode is ExecutionMode.IMMEDIATE_READ else 1_000,
        prepare_timeout_ms=2_000, submit_timeout_ms=10_000,
        execution_timeout_ms=120_000, reconcile_timeout_ms=15_000,
        idempotency=(IdempotencySupport.NATIVE if tool in REMOTE_READ_TOOLS
                     else IdempotencySupport.ADAPTER),
        cancellation=(CancellationSupport.SUPPORTED if tool in MEDIA_TOOLS | WORKSPACE_MUTATION_TOOLS
                      else CancellationSupport.BEST_EFFORT),
        query_status=tool not in REMOTE_READ_TOOLS,
        progress=tool in MEDIA_TOOLS | {"trigger_erp_sync"},
        callback=tool in MEDIA_TOOLS,
        result_schema_revision=1,
    )


def build_specialist_registry(
    providers: Mapping[str, SpecialistProvider],
) -> ExecutorRegistry:
    """Build an isolated non-production registry; missing providers fail closed."""
    registry = ExecutorRegistry()
    for tool in sorted(SPECIALIST_TOOLS):
        provider = providers.get(tool) or providers.get(SPECIALIST_EXECUTOR_TYPES[tool])
        if provider is None:
            raise ValueError(f"SPECIALIST_PROVIDER_MISSING:{tool}")
        descriptor = specialist_descriptor(tool)
        registry.register(
            descriptor,
            SpecialistExecutor(
                executor_type=descriptor.executor_type,
                revision=descriptor.revision,
                provider=provider,
                async_submit=descriptor.mode is not ExecutionMode.IMMEDIATE_READ,
            ),
            safety_level=SPECIALIST_SAFETY[tool],
        )
    return registry


def specialist_tool_names() -> frozenset[str]:
    return SPECIALIST_TOOLS
