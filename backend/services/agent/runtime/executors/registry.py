"""Deterministic, fail-closed Executor registry."""

from __future__ import annotations

from typing import Iterable

from services.agent.runtime.executors.types import ExecutorDescriptor
from services.agent.runtime.ports.executor import ExecutorPort


class ExecutorRegistry:
    """Resolve exactly one descriptor and implementation per action kind."""

    def __init__(
        self,
        entries: Iterable[tuple[ExecutorDescriptor, ExecutorPort]] = (),
    ) -> None:
        self._by_type: dict[str, tuple[ExecutorDescriptor, ExecutorPort]] = {}
        self._action_to_type: dict[str, str] = {}
        for descriptor, executor in entries:
            self.register(descriptor, executor)

    def register(
        self, descriptor: ExecutorDescriptor, executor: ExecutorPort,
    ) -> None:
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
