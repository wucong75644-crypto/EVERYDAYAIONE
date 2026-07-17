"""Conversation Actor 数据库终态后的 Web 投递与资源释放。"""

from __future__ import annotations

from typing import Any, Mapping

from loguru import logger

from schemas.websocket import build_message_done, build_message_error
from services.message_utils import format_message
from services.task_limit_service import release_task_slot


class ActorTerminalDelivery:
    """以数据库终态为事实源发送 best-effort WebSocket 通知。"""

    def __init__(self, db: Any, websocket: Any) -> None:
        self._db = db
        self._websocket = websocket

    async def notify(
        self,
        task: Mapping[str, Any],
        terminal_result: Mapping[str, Any],
    ) -> None:
        if terminal_result.get("outcome") in {
            "ownership_lost",
            "lease_expired",
        }:
            return
        current = await self._load_task(str(task["id"]))
        status = current.get("status")
        if status not in {"completed", "failed", "cancelled"}:
            return
        await release_task_slot(current)
        if status == "cancelled":
            return
        if _delivery_channel(current) == "wecom":
            return
        if status == "completed":
            await self._send_completed(current)
        else:
            await self._send_failed(current)

    async def _send_completed(self, task: Mapping[str, Any]) -> None:
        message = await self._load_message(str(task["assistant_message_id"]))
        push_task_id = _push_task_id(task)
        await self._websocket.send_to_task_or_user(
            push_task_id,
            str(task["user_id"]),
            build_message_done(
                task_id=push_task_id,
                conversation_id=str(task["conversation_id"]),
                message=format_message(message),
                credits_consumed=int(message.get("credits_cost") or 0),
            ),
            org_id=task.get("org_id"),
        )

    async def _send_failed(self, task: Mapping[str, Any]) -> None:
        push_task_id = _push_task_id(task)
        await self._websocket.send_to_task_or_user(
            push_task_id,
            str(task["user_id"]),
            build_message_error(
                task_id=push_task_id,
                conversation_id=str(task["conversation_id"]),
                message_id=str(task["assistant_message_id"]),
                error_code="GENERATION_FAILED",
                error_message=str(task.get("error_message") or "生成失败"),
            ),
            org_id=task.get("org_id"),
        )

    async def _load_task(self, task_id: str) -> dict[str, Any]:
        result = await (
            self._db.table("tasks")
            .select("*")
            .eq("id", task_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise RuntimeError("ACTOR_DELIVERY_TASK_MISSING")
        return dict(result.data)

    async def _load_message(self, message_id: str) -> dict[str, Any]:
        result = await (
            self._db.table("messages")
            .select("*")
            .eq("id", message_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise RuntimeError("ACTOR_DELIVERY_MESSAGE_MISSING")
        return dict(result.data)


def _push_task_id(task: Mapping[str, Any]) -> str:
    value = (
        task.get("client_task_id")
        or task.get("external_task_id")
        or task.get("id")
    )
    if not value:
        logger.error(f"actor_delivery_task_id_missing | task={task.get('id')}")
        raise RuntimeError("ACTOR_DELIVERY_TASK_ID_MISSING")
    return str(value)


def _delivery_channel(task: Mapping[str, Any]) -> str | None:
    context = task.get("delivery_context")
    if not isinstance(context, Mapping):
        return None
    channel = context.get("channel")
    return str(channel) if channel else None
