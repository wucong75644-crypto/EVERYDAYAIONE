"""Conversation Actor 跨进程控制事件的数据库适配器。"""

from __future__ import annotations

from typing import Any, Protocol

from psycopg.types.json import Jsonb

from services.conversation_commands import CommandType, ConversationCommand


class ConversationCommandStore(Protocol):
    async def append(
        self,
        *,
        conversation_id: str,
        task_id: str,
        turn_id: str | None,
        command_type: CommandType,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """持久化一个跨进程控制事件。"""

    async def load_pending(
        self,
        *,
        task_id: str,
        execution_token: str,
    ) -> list[ConversationCommand]:
        """读取当前 fencing owner 尚未应用的控制事件。"""

    async def acknowledge(
        self,
        *,
        event_id: str,
        task_id: str,
        execution_token: str,
        outcome: str = "applied",
    ) -> None:
        """以当前 fencing token 确认事件已应用。"""


class DatabaseConversationCommandStore:
    """通过 PostgreSQL RPC 读取并确认跨进程控制事件。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def append(
        self,
        *,
        conversation_id: str,
        task_id: str,
        turn_id: str | None,
        command_type: CommandType,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command_type not in {
            CommandType.CANCEL,
            CommandType.APPROVAL_RESULT,
            CommandType.SUBTASK_COMPLETED,
            CommandType.TOOL_COMPLETED,
        }:
            raise ValueError("command type cannot be persisted as a control event")
        response = await self._db.rpc(
            "append_conversation_control_command",
            {
                "p_conversation_id": conversation_id,
                "p_task_id": task_id,
                "p_turn_id": turn_id,
                "p_event_type": command_type.value,
                "p_dedupe_key": dedupe_key,
                "p_payload": Jsonb(payload),
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_CONTROL_APPEND_RESULT_INVALID")
        return data

    async def load_pending(
        self,
        *,
        task_id: str,
        execution_token: str,
    ) -> list[ConversationCommand]:
        response = await self._db.rpc(
            "read_conversation_control_commands",
            {
                "p_task_id": task_id,
                "p_execution_token": execution_token,
                "p_limit": 50,
            },
        ).execute()
        data = response.data if response else None
        if data is None:
            return []
        if not isinstance(data, list):
            raise RuntimeError("ACTOR_CONTROL_COMMAND_RESULT_INVALID")
        return [_command_from_row(row) for row in data]

    async def acknowledge(
        self,
        *,
        event_id: str,
        task_id: str,
        execution_token: str,
        outcome: str = "applied",
    ) -> None:
        response = await self._db.rpc(
            "acknowledge_conversation_control_command",
            {
                "p_event_id": event_id,
                "p_task_id": task_id,
                "p_execution_token": execution_token,
                "p_outcome": outcome,
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_CONTROL_ACK_RESULT_INVALID")
        if data.get("outcome") in {"ownership_lost", "terminal"}:
            raise RuntimeError("ACTOR_CONTROL_ACK_" + str(data["outcome"]).upper())


def _command_from_row(row: Any) -> ConversationCommand:
    if not isinstance(row, dict):
        raise RuntimeError("ACTOR_CONTROL_COMMAND_ROW_INVALID")
    try:
        command_type = CommandType(str(row["event_type"]))
        return ConversationCommand(
            command_id=str(row["id"]),
            command_type=command_type,
            conversation_id=str(row["conversation_id"]),
            task_id=str(row["task_id"]),
            turn_id=str(row["turn_id"]) if row.get("turn_id") else None,
            payload=row.get("payload") if isinstance(row.get("payload"), dict) else {},
            event_id=str(row["id"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("ACTOR_CONTROL_COMMAND_ROW_INVALID") from error
