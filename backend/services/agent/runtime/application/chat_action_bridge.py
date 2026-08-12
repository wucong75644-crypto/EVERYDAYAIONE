"""Chat-to-Runtime Action boundary.

The chat loop may only execute a tool through an explicitly injected Runtime
composition adapter.  There is intentionally no legacy executor fallback:
until the adapter can persist Policy/Dispatch facts, chat fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any, Mapping, Protocol


class RuntimeChatActionOwnershipError(RuntimeError):
    """Raised when chat has no Runtime-owned Action execution boundary."""


@dataclass(frozen=True, kw_only=True)
class ChatActionRequest:
    tool_name: str
    arguments: Mapping[str, Any]
    task_id: str
    conversation_id: str
    message_id: str
    user_id: str
    turn: int


class RuntimeChatActionExecutor(Protocol):
    async def execute(self, request: ChatActionRequest) -> Any: ...


class RuntimeChatActionLoopAdapter:
    """Composition seam from chat into the Runtime ActionLoop owner.

    ``dispatch`` must be supplied by the Runtime composition root.  This
    adapter deliberately contains no registry lookup or business capability;
    those remain owned by Runtime Policy/Dispatch/Catalog/Executor services.
    """

    def __init__(
        self,
        dispatch: Callable[[ChatActionRequest], Awaitable[Any]] | None,
    ) -> None:
        self._dispatch = dispatch

    async def execute(self, request: ChatActionRequest) -> Any:
        if self._dispatch is None:
            raise RuntimeChatActionOwnershipError(
                "RUNTIME_CHAT_ACTION_DISPATCH_NOT_WIRED"
            )
        return await self._dispatch(request)


class FailClosedRuntimeChatActionExecutor:
    """Default boundary used until a complete Runtime adapter is injected."""

    async def execute(self, request: ChatActionRequest) -> Any:
        del request
        raise RuntimeChatActionOwnershipError(
            "RUNTIME_CHAT_ACTION_EXECUTOR_NOT_WIRED"
        )


__all__ = [
    "ChatActionRequest",
    "FailClosedRuntimeChatActionExecutor",
    "RuntimeChatActionLoopAdapter",
    "RuntimeChatActionExecutor",
    "RuntimeChatActionOwnershipError",
]
