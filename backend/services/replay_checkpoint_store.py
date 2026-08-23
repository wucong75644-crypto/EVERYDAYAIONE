"""Conversation Actor ReplayCheckpoint 的 PostgreSQL 适配器。

该 Store 只负责可重放边界的持久读写；前端实时进度仍由 ActorWebSink 维护。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from psycopg.types.json import Jsonb


class ReplayCheckpointBoundary(str, Enum):
    BEFORE_MODEL = "before_model"
    AFTER_TOOL = "after_tool"
    BEFORE_COMMIT = "before_commit"


class ReplayCheckpointStore(Protocol):
    async def write(
        self,
        *,
        task_id: str,
        execution_token: str,
        boundary: ReplayCheckpointBoundary,
        payload: dict[str, Any],
        context_revision: int | None = None,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """以当前 fencing owner 写入一个幂等 checkpoint。"""

    async def read_latest(
        self,
        *,
        task_id: str,
        execution_token: str,
        boundary: ReplayCheckpointBoundary | None = None,
    ) -> dict[str, Any]:
        """按当前 fencing token 读取最近可重放 checkpoint。"""


class DatabaseReplayCheckpointStore:
    """通过 PostgreSQL RPC 写入/读取 ReplayCheckpoint。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def write(
        self,
        *,
        task_id: str,
        execution_token: str,
        boundary: ReplayCheckpointBoundary,
        payload: dict[str, Any],
        context_revision: int | None = None,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        state = dict(payload)
        state.setdefault("safe_point", boundary.value)
        if context_revision is not None:
            state.setdefault("context_revision", context_revision)
        if checkpoint_id is not None:
            state.setdefault("checkpoint_id", checkpoint_id)
        response = await self._db.rpc(
            "save_generation_checkpoint",
            {
                "p_task_id": task_id,
                "p_execution_token": execution_token,
                "p_safe_point": boundary.value,
                "p_state": Jsonb(state),
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_REPLAY_CHECKPOINT_WRITE_RESULT_INVALID")
        return data

    async def read_latest(
        self,
        *,
        task_id: str,
        execution_token: str,
        boundary: ReplayCheckpointBoundary | None = None,
    ) -> dict[str, Any]:
        response = await self._db.rpc(
            "load_generation_checkpoint",
            {
                "p_task_id": task_id,
                "p_execution_token": execution_token,
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_REPLAY_CHECKPOINT_READ_RESULT_INVALID")
        if data.get("outcome") == "empty":
            return {"outcome": "not_found"}
        if data.get("outcome") != "loaded":
            return data
        state = data.get("state")
        if not isinstance(state, dict):
            raise RuntimeError("ACTOR_REPLAY_CHECKPOINT_STATE_INVALID")
        payload = state.get("payload")
        if isinstance(payload, dict):
            state = payload
        else:
            state = dict(state)
        if "content_blocks" not in state and isinstance(state.get("blocks"), list):
            state["content_blocks"] = state["blocks"]
        result = {
            "outcome": "found",
            "boundary": data.get("safe_point"),
            "version": data.get("version"),
            "payload": state,
        }
        if boundary is not None and result["boundary"] not in {
            boundary.value,
            None,
        }:
            return {"outcome": "not_found"}
        return result
