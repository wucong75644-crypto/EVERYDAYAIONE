"""Deterministic, fail-closed Executor registry."""

from __future__ import annotations

from typing import Iterable

from services.agent.runtime.executors.types import ExecutorDescriptor
from services.agent.runtime.executors.types import (
    ExecutionMode,
    IdempotencySupport,
)
from services.agent.runtime.ports.executor import ExecutorPort


class ExecutorRegistry:
    """Resolve exactly one descriptor and implementation per action kind."""

    def __init__(
        self,
        entries: Iterable[tuple[ExecutorDescriptor, ExecutorPort]] = (),
    ) -> None:
        self._by_type: dict[str, tuple[ExecutorDescriptor, ExecutorPort]] = {}
        self._action_to_type: dict[str, str] = {}
        self._safety_by_action: dict[str, str] = {}
        self.specialist_facts: object | None = None
        for descriptor, executor in entries:
            self.register(descriptor, executor)

    def register(
        self, descriptor: ExecutorDescriptor, executor: ExecutorPort,
        *, safety_level: str | None = None,
    ) -> None:
        side_effecting = descriptor.mode not in {
            ExecutionMode.IMMEDIATE_READ,
            ExecutionMode.LOCAL_RENDER,
        }
        if (
            side_effecting
            and descriptor.idempotency is IdempotencySupport.NONE
            and not descriptor.query_status
        ):
            raise ValueError(
                "side-effecting executor requires recovery capability",
            )
        if descriptor.executor_type in self._by_type:
            raise ValueError(
                f"duplicate executor type: {descriptor.executor_type}",
            )
        conflicts = descriptor.action_kinds.intersection(self._action_to_type)
        if conflicts:
            raise ValueError(
                f"duplicate action kinds: {sorted(conflicts)}",
            )
        self._by_type[descriptor.executor_type] = (descriptor, executor)
        for action_kind in sorted(descriptor.action_kinds):
            self._action_to_type[action_kind] = descriptor.executor_type
            if safety_level is not None:
                self._safety_by_action[action_kind] = safety_level

    def register_read(
        self, descriptor: ExecutorDescriptor, executor: ExecutorPort,
        *, safety_level: str,
    ) -> None:
        """Register a read Executor only with an explicit safe declaration."""
        if descriptor.mode is not ExecutionMode.IMMEDIATE_READ:
            raise ValueError("read executor must use immediate_read mode")
        if safety_level != "safe":
            raise ValueError("read executor safety must be safe")
        self.register(descriptor, executor, safety_level=safety_level)

    def resolve(
        self, action_kind: str,
    ) -> tuple[ExecutorDescriptor, ExecutorPort]:
        executor_type = self._action_to_type.get(action_kind)
        if executor_type is None:
            raise LookupError(f"executor not registered: {action_kind}")
        return self._by_type[executor_type]

    def descriptors(self) -> tuple[ExecutorDescriptor, ...]:
        return tuple(
            self._by_type[key][0] for key in sorted(self._by_type)
        )

    def safety_level(self, action_kind: str) -> str | None:
        return self._safety_by_action.get(action_kind)
