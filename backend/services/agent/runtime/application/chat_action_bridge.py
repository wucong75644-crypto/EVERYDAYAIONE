"""Chat-to-Runtime Action boundary.

The chat loop may only execute a tool through an explicitly injected Runtime
composition adapter.  There is intentionally no legacy executor fallback:
until the adapter can persist Policy/Dispatch facts, chat fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any, Mapping, Protocol

from services.agent.runtime.ports.action_repository import (
    ActionMutationReceipt, ChatActionSubmissionPort,
)


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
    tool_call_id: str = ""
    org_id: str | None = None
    model_id: str | None = None
    model_provider: str | None = None
    model_revision: str | None = None
    catalog_revision: str | None = None
    context_receipt: Mapping[str, object] | None = None

    def persistence_payload(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "user_id": self.user_id,
            "turn": self.turn,
            "tool_call_id": self.tool_call_id or self.message_id,
            "org_id": self.org_id,
            "model_id": self.model_id,
            "model_provider": self.model_provider,
            "model_revision": self.model_revision,
            "catalog_revision": self.catalog_revision,
            "context_receipt": dict(self.context_receipt or {}),
        }


class RuntimeChatActionExecutor(Protocol):
    async def execute(self, request: ChatActionRequest) -> Any: ...


class RuntimeChatActionPersistenceExecutor:
    """Submit chat Actions to Runtime and never execute business code locally."""

    def __init__(
        self, *, submission: ChatActionSubmissionPort,
        registry: Any, policy_revision: str = "runtime-chat-v1",
    ) -> None:
        self._submission = submission
        self._registry = registry
        self._policy_revision = policy_revision

    async def execute(self, request: ChatActionRequest) -> Any:
        try:
            descriptor, _executor = self._registry.resolve(request.tool_name)
        except (LookupError, AttributeError) as error:
            raise RuntimeChatActionOwnershipError(
                "RUNTIME_CHAT_ACTION_CATALOG_MISSING"
            ) from error
        safety_level = self._registry.safety_level(request.tool_name)
        if safety_level is None:
            raise RuntimeChatActionOwnershipError(
                "RUNTIME_CHAT_ACTION_SAFETY_UNDECLARED"
            )
        snapshot = {
            "source": "chat",
            "policy_revision": self._policy_revision,
            "executor_type": descriptor.executor_type,
            "executor_revision": descriptor.revision,
            "tool_name": request.tool_name,
            "safety_level": safety_level,
            "scope": {"org_id": request.org_id, "user_id": request.user_id},
        }
        receipt = await self._submission.submit_chat_action(
            request=request.persistence_payload(),
            policy_snapshot=snapshot,
            policy_revision=self._policy_revision,
            executor_type=descriptor.executor_type,
            executor_revision=descriptor.revision,
        )
        if receipt.outcome.value not in {"claimed", "found", "created"}:
            return _receipt_text(receipt)
        return _receipt_text(receipt)


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
    "RuntimeChatActionPersistenceExecutor",
    "RuntimeChatActionExecutor",
    "RuntimeChatActionOwnershipError",
]


def _receipt_text(receipt: ActionMutationReceipt) -> str:
    """Expose durable state without making chat retry accepted/unknown work."""
    outcome = receipt.outcome.value
    action_id = receipt.action_id or "pending"
    return f"Runtime Action {outcome}; action_id={action_id}; reconcile_by_runtime=true"
