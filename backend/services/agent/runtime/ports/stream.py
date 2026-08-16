"""Runtime stream event transport contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Protocol

from services.agent.runtime.domain.identity import require_stable_value


@dataclass(frozen=True, kw_only=True)
class RuntimeStreamTarget:
    """Immutable routing identity for one user-visible Runtime response."""

    task_id: str
    user_id: str
    conversation_id: str
    message_id: str
    org_id: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.task_id, "stream task_id"),
            (self.user_id, "stream user_id"),
            (self.conversation_id, "stream conversation_id"),
            (self.message_id, "stream message_id"),
        ):
            require_stable_value(value, name)
        if self.org_id is not None:
            require_stable_value(self.org_id, "stream org_id")


class RuntimeStreamPublisher(Protocol):
    """Publishes transport-neutral messages to the configured event bus."""

    def publish(
        self,
        *,
        target: RuntimeStreamTarget,
        message: Mapping[str, object],
    ) -> Awaitable[None]: ...

    def close(self) -> Awaitable[None]: ...
