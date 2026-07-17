"""生成任务的操作者身份与资源作用域。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from core.workspace import build_wecom_channel_workspace_owner


@dataclass(frozen=True)
class ExecutionScope:
    """分离真实操作者、会话作用域与 Workspace owner。"""

    actor_user_id: str
    context_scope: Literal["user", "channel"]
    workspace_owner_id: str
    personal_context_allowed: bool


async def resolve_execution_scope(
    db: Any,
    task: Mapping[str, Any],
    conversation_id: str,
) -> ExecutionScope:
    """从数据库会话和渠道投递事实解析可信执行作用域。"""
    response = await (
        db.table("conversations")
        .select("id,org_id,user_id,source,scope_type,scope_id")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    row = response.data if response else None
    actor_user_id = str(task.get("user_id") or "")
    if (
        not row
        or not actor_user_id
        or row.get("id") != conversation_id
        or row.get("org_id") != task.get("org_id")
    ):
        raise RuntimeError("ACTOR_EXECUTION_SCOPE_MISMATCH")

    scope_type = row.get("scope_type") or "user"
    if scope_type == "user":
        if str(row.get("user_id") or "") != actor_user_id:
            raise RuntimeError("ACTOR_EXECUTION_SCOPE_MISMATCH")
        return ExecutionScope(
            actor_user_id=actor_user_id,
            context_scope="user",
            workspace_owner_id=actor_user_id,
            personal_context_allowed=True,
        )
    if scope_type != "channel":
        raise RuntimeError("ACTOR_EXECUTION_SCOPE_UNSUPPORTED")

    delivery = _parse_delivery_context(task.get("delivery_context"))
    chat_id = str(delivery.get("chatid") or "")
    corp_id = str(delivery.get("corp_id") or "")
    if (
        row.get("source") != "wecom"
        or row.get("user_id") is not None
        or str(row.get("scope_id") or "") != chat_id
        or delivery.get("channel") != "wecom"
        or delivery.get("chattype") != "group"
    ):
        raise RuntimeError("ACTOR_EXECUTION_SCOPE_MISMATCH")
    return ExecutionScope(
        actor_user_id=actor_user_id,
        context_scope="channel",
        workspace_owner_id=build_wecom_channel_workspace_owner(
            corp_id, chat_id,
        ),
        personal_context_allowed=False,
    )


def _parse_delivery_context(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    return {}
