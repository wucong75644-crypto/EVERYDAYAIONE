"""企微显式记忆指令的数据库能力适配。"""

from __future__ import annotations

from typing import Any


def get_wecom_manual_memories(
    db: Any, *, user_id: str, org_id: str,
) -> list[dict[str, Any]]:
    response = db.rpc("get_wecom_manual_memories", {
        "p_user_id": user_id,
        "p_org_id": org_id,
    }).execute()
    return response.data if isinstance(response.data, list) else []


def clear_wecom_manual_memories(
    db: Any, *, user_id: str, org_id: str,
) -> None:
    response = db.rpc("clear_wecom_manual_memories", {
        "p_user_id": user_id,
        "p_org_id": org_id,
    }).execute()
    if not isinstance(response.data, dict) or response.data.get(
        "outcome"
    ) != "cleared":
        raise RuntimeError("WECOM_MEMORY_CLEAR_INVALID")
