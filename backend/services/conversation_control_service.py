"""Conversation control state lookup and execution.

The semantic router only decides *what the user means*.  This module owns the
authoritative task lookup and delegates the actual transition to the existing
PostgreSQL Actor RPCs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.conversation_control_router import (
    ConversationControlState,
    ControlAction,
)
from services.conversation_task import (
    control_actor_task,
    is_actor_task,
    pause_actor_task,
    resume_actor_task,
)


_CONTROL_STATUSES = ["pending", "running", "pausing", "paused"]
_TASK_FIELDS = (
    "id, external_task_id, client_task_id, conversation_id, turn_id, "
    "assistant_message_id, input_message_id, user_id, org_id, type, status, "
    "delivery_context, request_params, created_at"
)


@dataclass(frozen=True)
class ConversationControlTasks:
    """The newest controllable Actor task in each lifecycle state."""

    running: Mapping[str, Any] | None = None
    paused: Mapping[str, Any] | None = None

    @property
    def state(self) -> str:
        if self.running:
            return "running"
        if self.paused:
            return "paused"
        return "idle"

    def to_router_state(self) -> ConversationControlState:
        return ConversationControlState(
            state=self.state,
            has_running_task=self.running is not None,
            has_paused_task=self.paused is not None,
            latest_task_summary="当前对话中的最近任务",
        )


def load_control_tasks(
    db: Any,
    *,
    conversation_id: str,
    user_id: str,
    org_id: str | None,
) -> ConversationControlTasks:
    """Load only user-owned Actor tasks in the requested tenant scope."""
    query = db.table("tasks").select(_TASK_FIELDS).eq(
        "conversation_id", conversation_id,
    ).eq("user_id", user_id).eq("type", "chat").in_(
        "status", _CONTROL_STATUSES,
    ).order("created_at", desc=True).limit(20)
    result = query.execute()
    rows = result.data if result and isinstance(result.data, list) else []

    running: Mapping[str, Any] | None = None
    paused: Mapping[str, Any] | None = None
    for task in rows:
        if not isinstance(task, Mapping) or not _org_matches(task, org_id):
            continue
        if not is_actor_task(task):
            continue
        status = task.get("status")
        if status in {"pending", "running", "pausing"} and running is None:
            running = task
        elif status == "paused" and paused is None:
            paused = task
    return ConversationControlTasks(running=running, paused=paused)


def execute_control_action(
    db: Any,
    *,
    tasks: ConversationControlTasks,
    action: ControlAction,
    user_id: str,
    org_id: str | None,
) -> dict[str, Any]:
    """Execute a classified action through the existing fenced RPC path."""
    if action is ControlAction.PAUSE:
        if not tasks.running:
            return {"action": action.value, "outcome": "no_running_task"}
        result = pause_actor_task(db, tasks.running, user_id, org_id)
        return _result(action, tasks.running, result)

    if action is ControlAction.CANCEL:
        task = tasks.running or tasks.paused
        if not task:
            return {"action": action.value, "outcome": "no_active_task"}
        result = control_actor_task(
            db, task, user_id, org_id, "cancel",
        )
        return _result(action, task, result)

    if action is ControlAction.RESUME:
        if not tasks.paused:
            return {"action": action.value, "outcome": "no_paused_task"}
        result = resume_actor_task(db, tasks.paused, user_id, org_id)
        return _result(action, tasks.paused, result)

    return {"action": ControlAction.NONE.value, "outcome": "ignored"}


def _result(
    action: ControlAction,
    task: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "action": action.value,
        **dict(result),
        "task_id": str(task.get("id")) if task.get("id") else None,
        "client_task_id": task.get("client_task_id"),
        "external_task_id": task.get("external_task_id"),
        "assistant_message_id": task.get("assistant_message_id"),
        "conversation_id": task.get("conversation_id"),
    }


def _org_matches(task: Mapping[str, Any], org_id: str | None) -> bool:
    task_org_id = task.get("org_id")
    return task_org_id == org_id if org_id else task_org_id is None
