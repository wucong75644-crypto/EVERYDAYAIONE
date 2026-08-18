"""Conversation Actor 父子任务关联适配器。"""

from __future__ import annotations

from typing import Any, Protocol


class ConversationSubtaskStore(Protocol):
    async def register(
        self,
        *,
        parent_task_id: str,
        parent_execution_token: str,
        parent_command_id: str,
        child_task_id: str,
    ) -> dict[str, Any]:
        """以父任务当前 fencing owner 注册一个子任务。"""


class DatabaseConversationSubtaskStore:
    """通过 PostgreSQL RPC 注册父子任务关系。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def register(
        self,
        *,
        parent_task_id: str,
        parent_execution_token: str,
        parent_command_id: str,
        child_task_id: str,
    ) -> dict[str, Any]:
        response = await self._db.rpc(
            "register_conversation_subtask",
            {
                "p_parent_task_id": parent_task_id,
                "p_parent_execution_token": parent_execution_token,
                "p_parent_command_id": parent_command_id,
                "p_child_task_id": child_task_id,
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_SUBTASK_REGISTER_RESULT_INVALID")
        return data
