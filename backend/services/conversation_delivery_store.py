"""Conversation Actor 页面交付会话的 PostgreSQL 适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class DeliverySession:
    session_id: str
    stream_id: str
    execution_attempt: int
    next_seq: int
    snapshot_seq: int
    snapshot_content: str
    snapshot_blocks: list[dict[str, Any]]


class ConversationDeliveryStore(Protocol):
    async def begin(
        self,
        *,
        task_id: str,
        execution_token: str,
        execution_attempt: int,
        message_id: str,
    ) -> DeliverySession:
        """为当前 fencing owner 建立或恢复一个页面交付会话。"""

    async def append(
        self,
        *,
        task_id: str,
        execution_token: str,
        event_type: str,
        payload: Mapping[str, Any],
        event_id: str,
    ) -> dict[str, Any]:
        """持久化一个有序交付事件并返回序号；event_id 用于安全重试。"""

    async def save_snapshot(
        self,
        *,
        task_id: str,
        execution_token: str,
        content: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """保存最近可用于刷新恢复的页面快照。"""


class DatabaseConversationDeliveryStore:
    """通过 PostgreSQL RPC 管理页面交付会话和事件。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def begin(
        self,
        *,
        task_id: str,
        execution_token: str,
        execution_attempt: int,
        message_id: str,
    ) -> DeliverySession:
        response = await self._db.rpc(
            "begin_conversation_delivery_session",
            {
                "p_task_id": task_id,
                "p_execution_token": execution_token,
                "p_execution_attempt": execution_attempt,
                "p_message_id": message_id,
            },
        ).execute()
        data = _response_dict(response, "DELIVERY_SESSION_BEGIN_RESULT_INVALID")
        if data.get("outcome") in {"ownership_lost", "terminal"}:
            raise RuntimeError(
                "DELIVERY_SESSION_BEGIN_" + str(data["outcome"]).upper()
            )
        return _session_from_data(data)

    async def append(
        self,
        *,
        task_id: str,
        execution_token: str,
        event_type: str,
        payload: Mapping[str, Any],
        event_id: str,
    ) -> dict[str, Any]:
        response = await self._db.rpc(
            "append_conversation_delivery_event",
            {
                "p_task_id": task_id,
                "p_execution_token": execution_token,
                "p_event_type": event_type,
                "p_payload": Jsonb(dict(payload)),
                "p_event_id": event_id,
            },
        ).execute()
        data = _response_dict(response, "DELIVERY_EVENT_APPEND_RESULT_INVALID")
        if data.get("outcome") in {"ownership_lost", "terminal"}:
            raise RuntimeError(
                "DELIVERY_EVENT_APPEND_" + str(data["outcome"]).upper()
            )
        return data

    async def save_snapshot(
        self,
        *,
        task_id: str,
        execution_token: str,
        content: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        response = await self._db.rpc(
            "save_conversation_delivery_snapshot",
            {
                "p_task_id": task_id,
                "p_execution_token": execution_token,
                "p_content": content,
                "p_blocks": Jsonb(blocks),
            },
        ).execute()
        data = _response_dict(response, "DELIVERY_SNAPSHOT_RESULT_INVALID")
        if data.get("outcome") == "ownership_lost":
            raise RuntimeError("DELIVERY_SNAPSHOT_OWNERSHIP_LOST")
        return data


def _response_dict(response: Any, error_code: str) -> dict[str, Any]:
    data = response.data if response else None
    if not isinstance(data, dict):
        raise RuntimeError(error_code)
    return data


def _session_from_data(data: Mapping[str, Any]) -> DeliverySession:
    required = ("session_id", "stream_id", "execution_attempt")
    if any(not data.get(field) for field in required):
        raise RuntimeError("DELIVERY_SESSION_DATA_INVALID")
    blocks = data.get("snapshot_blocks")
    if not isinstance(blocks, list):
        blocks = []
    return DeliverySession(
        session_id=str(data["session_id"]),
        stream_id=str(data["stream_id"]),
        execution_attempt=int(data["execution_attempt"]),
        next_seq=int(data.get("next_seq") or 0),
        snapshot_seq=int(data.get("snapshot_seq") or 0),
        snapshot_content=str(data.get("snapshot_content") or ""),
        snapshot_blocks=[block for block in blocks if isinstance(block, dict)],
    )
