"""Conversation Actor 任务识别与取消入口。"""

from __future__ import annotations

import json
from typing import Any, Mapping

from psycopg.types.json import Jsonb


def is_actor_task(task: Mapping[str, Any]) -> bool:
    context = task.get("delivery_context")
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except (TypeError, ValueError):
            return False
    return isinstance(context, dict) and context.get("actor") is True


def control_actor_task(
    db: Any,
    task: Mapping[str, Any],
    user_id: str,
    org_id: str | None,
    control: str,
) -> dict[str, Any]:
    if control not in {"cancel", "pause"}:
        raise ValueError("unsupported actor control")

    if control == "cancel":
        rpc_name = (
            "cancel_paused_generation_turn"
            if task.get("status") == "paused"
            else "cancel_generation_turn"
        )
        response = db.rpc(
            rpc_name,
            {
                "p_task_id": str(task["id"]),
                "p_user_id": user_id,
                "p_org_id": org_id,
            },
        ).execute()
        result = response.data if response else None
        if not isinstance(result, dict):
            raise RuntimeError("ACTOR_CANCEL_RESULT_INVALID")
        return result

    response = db.rpc(
        "append_conversation_control_command",
        {
            "p_conversation_id": str(task["conversation_id"]),
            "p_task_id": str(task["id"]),
            "p_turn_id": task.get("turn_id"),
            "p_event_type": "pause",
            "p_dedupe_key": f"pause:{task['id']}",
            "p_payload": Jsonb({"reason": "user_pause", "user_id": user_id}),
            "p_org_id": org_id,
        },
    ).execute()
    result = response.data if response else None
    if not isinstance(result, dict):
        raise RuntimeError("ACTOR_CONTROL_RESULT_INVALID")
    if result.get("outcome") == "enqueued":
        return {**result, "outcome": "requested"}
    return result


def cancel_actor_task(
    db: Any,
    task: Mapping[str, Any],
    user_id: str,
    org_id: str | None,
) -> bool:
    result = control_actor_task(db, task, user_id, org_id, "cancel")
    outcome = result.get("outcome")
    if outcome in {"cancelled", "already_cancelled", "requested"}:
        return True
    if outcome == "terminal":
        return False
    raise RuntimeError(f"ACTOR_CANCEL_FAILED:{outcome or 'unknown'}")


def pause_actor_task(
    db: Any,
    task: Mapping[str, Any],
    user_id: str,
    org_id: str | None,
) -> dict[str, Any]:
    """请求暂停；运行中只入队，必须由 Runtime 安全点完成快照。"""
    result = control_actor_task(db, task, user_id, org_id, "pause")
    if result.get("outcome") not in {
        "requested", "paused", "already_paused",
    }:
        if result.get("outcome") == "terminal":
            return result
        raise RuntimeError(
            f"ACTOR_PAUSE_FAILED:{result.get('outcome') or 'unknown'}"
        )
    return result


def resume_actor_task(
    db: Any,
    task: Mapping[str, Any],
    user_id: str,
    org_id: str | None,
) -> dict[str, Any]:
    """把 paused Actor task 原子放回 pending，等待新 execution token。"""
    response = db.rpc(
        "resume_paused_generation_turn",
        {
            "p_task_id": str(task["id"]),
            "p_user_id": user_id,
            "p_org_id": org_id,
        },
    ).execute()
    result = response.data if response else None
    if not isinstance(result, dict):
        raise RuntimeError("ACTOR_RESUME_RESULT_INVALID")
    return result
