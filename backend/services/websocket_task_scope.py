"""WebSocket 任务订阅的用户与租户范围校验。"""

from __future__ import annotations

from typing import Any


TASK_ID_FIELDS = ("client_task_id", "external_task_id", "id")


def find_task_in_connection_scope(
    db: Any,
    task_id: str,
    user_id: str,
    org_id: str | None,
) -> dict[str, Any] | None:
    """只在当前 WebSocket 连接的用户与组织范围内解析任务。"""
    for field in TASK_ID_FIELDS:
        query = db.table("tasks").select("*").eq(
            field, task_id,
        ).eq("user_id", user_id)
        query = (
            query.eq("org_id", org_id)
            if org_id is not None
            else query.is_("org_id", "null")
        )
        result = query.maybe_single().execute()
        if result and result.data:
            return result.data
    return None
