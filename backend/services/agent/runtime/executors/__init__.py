"""Executor registry, descriptors, and restricted capabilities."""

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.resolver import (
    ActionExecutorResolver,
    PostgresActionExecutorResolver,
    ResolvedActionExecution,
)
from services.agent.runtime.executors.types import (
    AuthorizationRequirement,
    CancellationSupport,
    ExecutionMode,
    ExecutorDescriptor,
    IdempotencySupport,
)
from services.agent.runtime.executors.contracts import (
    ActionSnapshot, ResultPolicy, canonical_json, canonical_request_hash,
)
from services.agent.runtime.executors.read_only import (
    CallableReadCapability, ReadOnlyExecutor, ScopedReadCapability,
)
from services.agent.runtime.executors.read_registry import (
    READ_SCOPE_KINDS, READ_TOOL_SPECS, build_read_executor_registry,
    read_descriptor,
)
from services.agent.runtime.executors.capabilities import RestrictedArtifactCapability
from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.executors.real_composition import build_nonproduction_read_registry
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.specialist_registry import (
    SPECIALIST_TOOLS, SPECIALIST_SAFETY, build_specialist_registry, specialist_descriptor,
)

__all__ = [
    "AuthorizationRequirement",
    "ActionExecutorResolver",
    "CancellationSupport",
    "ExecutionMode",
    "ExecutorDescriptor",
    "ExecutorRegistry",
    "IdempotencySupport",
    "PostgresActionExecutorResolver",
    "ResolvedActionExecution",
    "ActionSnapshot", "ResultPolicy", "canonical_json",
    "canonical_request_hash", "CallableReadCapability", "ScopedReadCapability",
    "ReadOnlyExecutor",
    "READ_SCOPE_KINDS", "READ_TOOL_SPECS", "build_read_executor_registry",
    "read_descriptor",
    "RestrictedArtifactCapability",
    "RuntimeReadResources", "build_nonproduction_read_registry",
    "SpecialistExecutor", "SPECIALIST_TOOLS", "build_specialist_registry",
    "SPECIALIST_SAFETY", "specialist_descriptor",
]
