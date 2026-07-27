"""Executor registry, descriptors, and restricted capabilities."""

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.types import (
    AuthorizationRequirement,
    CancellationSupport,
    ExecutionMode,
    ExecutorDescriptor,
    IdempotencySupport,
)

__all__ = [
    "AuthorizationRequirement",
    "CancellationSupport",
    "ExecutionMode",
    "ExecutorDescriptor",
    "ExecutorRegistry",
    "IdempotencySupport",
]
