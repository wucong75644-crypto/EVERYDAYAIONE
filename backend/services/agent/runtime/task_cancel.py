"""Typed Web boundary for cancelling Runtime-owned tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol
from uuid import UUID


class TaskOwner(StrEnum):
    RUNTIME = "runtime"
    ACTOR = "actor"
    LEGACY = "legacy"
    AMBIGUOUS = "ambiguous"


class RuntimeTaskCancelOutcome(StrEnum):
    CANCELLED_BEFORE_CLAIM = "cancelled_before_claim"
    CANCELLED = "cancelled"
    ALREADY_CANCELLED = "already_cancelled"
    TERMINAL_CONFLICT = "terminal_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    ASSOCIATION_REJECTED = "association_rejected"


class RuntimeTaskCancelConflict(Exception):
    """The caller cannot prove a cancel is valid for the persisted task."""


class RuntimeTaskCancelUnavailable(Exception):
    """The committed cancel result is unavailable or malformed."""


@dataclass(frozen=True)
class RuntimeTaskCancelRequest:
    task_id: str
    message_id: str
    org_id: str | None
    user_id: str
    session_id: str
    submit_command_id: str
    idempotency_key: str


@dataclass(frozen=True)
class RuntimeTaskCancelReceipt:
    outcome: RuntimeTaskCancelOutcome


class RuntimeTaskCancelRepository(Protocol):
    def cancel(
        self, request: RuntimeTaskCancelRequest,
    ) -> RuntimeTaskCancelReceipt: ...


def _delivery_context(task: Mapping[str, Any]) -> Mapping[str, Any] | None:
    context = task.get("delivery_context")
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except (TypeError, ValueError) as exc:
            raise ValueError("TASK_DELIVERY_CONTEXT_INVALID") from exc
    if context is None or isinstance(context, Mapping):
        return context
    raise ValueError("TASK_DELIVERY_CONTEXT_INVALID")


def classify_task_owner(task: Mapping[str, Any]) -> TaskOwner:
    """Classify only canonical owner markers; malformed combinations close."""
    try:
        context = _delivery_context(task)
    except ValueError:
        return TaskOwner.AMBIGUOUS
    if context is None:
        return TaskOwner.LEGACY
    has_runtime = "runtime" in context
    has_actor = "actor" in context
    if not has_runtime and not has_actor:
        return TaskOwner.LEGACY
    runtime = context.get("runtime")
    actor = context.get("actor")
    if has_runtime and not isinstance(runtime, bool):
        return TaskOwner.AMBIGUOUS
    if has_actor and not isinstance(actor, bool):
        return TaskOwner.AMBIGUOUS
    if has_runtime and runtime is True and has_actor and actor is False:
        return TaskOwner.RUNTIME
    if has_actor and actor is True and (not has_runtime or runtime is False):
        return TaskOwner.ACTOR
    return TaskOwner.AMBIGUOUS


class RuntimeTaskCancelService:
    """Validate persisted Web bindings before invoking the atomic facade."""

    _SUCCESS = {
        RuntimeTaskCancelOutcome.CANCELLED_BEFORE_CLAIM,
        RuntimeTaskCancelOutcome.CANCELLED,
        RuntimeTaskCancelOutcome.ALREADY_CANCELLED,
    }

    def __init__(self, repository: RuntimeTaskCancelRepository) -> None:
        self._repository = repository

    def cancel_task(
        self, task: Mapping[str, Any], *, user_id: str, org_id: str | None,
    ) -> RuntimeTaskCancelReceipt:
        request = self.prepare_task(task, user_id=user_id, org_id=org_id)
        return self.cancel_prepared(request)

    def prepare_task(
        self, task: Mapping[str, Any], *, user_id: str, org_id: str | None,
    ) -> RuntimeTaskCancelRequest:
        """Validate all local bindings without causing a durable mutation."""
        if classify_task_owner(task) is not TaskOwner.RUNTIME:
            raise RuntimeTaskCancelConflict("RUNTIME_TASK_OWNER_INVALID")
        try:
            context = _delivery_context(task)
        except ValueError:
            context = None
        if context is None:
            raise RuntimeTaskCancelConflict("RUNTIME_TASK_CONTEXT_INVALID")
        task_id = self._uuid(task.get("id"))
        message_id = self._uuid(task.get("assistant_message_id"))
        session_id = self._uuid(context.get("runtime_session_id"))
        command_id = self._uuid(context.get("runtime_command_id"))
        requester = self._uuid(user_id)
        canonical_org = self._optional_uuid(org_id)
        if self._optional_uuid(task.get("org_id")) != canonical_org:
            raise RuntimeTaskCancelConflict("RUNTIME_TASK_ORG_MISMATCH")
        if self._uuid(task.get("user_id")) != requester:
            raise RuntimeTaskCancelConflict("RUNTIME_TASK_USER_MISMATCH")
        return RuntimeTaskCancelRequest(
            task_id=task_id,
            message_id=message_id,
            org_id=canonical_org,
            user_id=requester,
            session_id=session_id,
            submit_command_id=command_id,
            idempotency_key=f"web-task-cancel:{task_id}:{command_id}",
        )

    def cancel_prepared(
        self, request: RuntimeTaskCancelRequest,
    ) -> RuntimeTaskCancelReceipt:
        """Invoke the atomic facade for a request that passed local preflight."""
        receipt = self._repository.cancel(request)
        if receipt.outcome not in self._SUCCESS:
            raise RuntimeTaskCancelConflict("RUNTIME_TASK_CANCEL_CONFLICT")
        return receipt

    @staticmethod
    def _uuid(value: object) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuntimeTaskCancelConflict(
                "RUNTIME_TASK_BINDING_INVALID",
            ) from exc

    @classmethod
    def _optional_uuid(cls, value: object) -> str | None:
        return None if value is None else cls._uuid(value)
