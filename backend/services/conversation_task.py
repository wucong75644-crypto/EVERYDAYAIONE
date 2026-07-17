"""Conversation Actor 任务识别与取消入口。"""

from __future__ import annotations

import json
from typing import Any, Mapping


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
    response = db.rpc(
        "cancel_generation_turn",
        {
            "p_task_id": str(task["id"]),
            "p_user_id": user_id,
            "p_org_id": org_id,
        },
    ).execute()
    result = response.data if response else None
    if not isinstance(result, dict):
        raise RuntimeError("ACTOR_CANCEL_RESULT_INVALID")
    outcome = result.get("outcome")
    if outcome in {"cancelled", "already_cancelled"}:
        return True
    if outcome == "terminal":
        return False
    raise RuntimeError(f"ACTOR_CANCEL_FAILED:{outcome or 'unknown'}")
