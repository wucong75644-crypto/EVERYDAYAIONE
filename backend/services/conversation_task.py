"""Conversation Actor 任务识别与取消入口。"""

from __future__ import annotations

import json
from typing import Any, Mapping

from psycopg.types.json import Jsonb

from services.conversation_commands import CommandType


def is_actor_task(task: Mapping[str, Any]) -> bool:
    context = task.get("delivery_context")
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except (TypeError, ValueError):
            return False
    return isinstance(context, dict) and context.get("actor") is True


def cancel_actor_task(
    db: Any,
    task: Mapping[str, Any],
    user_id: str,
    org_id: str | None,
) -> bool:
    task_id = str(task["id"])
    response = db.rpc(
        "append_conversation_control_command",
        {
            "p_conversation_id": str(task["conversation_id"]),
            "p_task_id": task_id,
            "p_turn_id": str(task["turn_id"]) if task.get("turn_id") else None,
            "p_event_type": CommandType.CANCEL.value,
            "p_dedupe_key": f"cancel:{task_id}",
            "p_payload": Jsonb({"reason": "user_cancelled", "user_id": user_id}),
        },
    ).execute()
    result = response.data if response else None
    if not isinstance(result, dict):
        raise RuntimeError("ACTOR_CANCEL_RESULT_INVALID")
    outcome = result.get("outcome")
    if outcome in {"enqueued", "already_enqueued", "already_cancelled"}:
        return True
    if outcome == "terminal":
        return False
    raise RuntimeError(f"ACTOR_CANCEL_FAILED:{outcome or 'unknown'}")
