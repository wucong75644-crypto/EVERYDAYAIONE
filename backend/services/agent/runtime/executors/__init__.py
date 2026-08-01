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
]
