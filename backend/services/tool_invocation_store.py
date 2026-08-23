"""Conversation Actor 的副作用工具持久化幂等记录。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from psycopg.types.json import Jsonb


class ToolInvocationStore(Protocol):
    def mark_stale(
        self,
        *,
        task_id: str,
        turn_id: str,
        tool_call_id: str,
        execution_token: str,
        stale_after_seconds: int = 900,
    ) -> dict[str, Any]:
        """将超时仍 running 的调用转为 uncertain。"""

    def begin(
        self,
        *,
        task_id: str,
        conversation_id: str,
        turn_id: str,
        execution_token: str,
        tool_call_id: str,
        tool_name: str,
        args_hash: str,
    ) -> dict[str, Any]:
        """登记工具调用并返回 execute/replay/uncertain 决策。"""

    def complete(
        self,
        *,
        task_id: str,
        turn_id: str,
        tool_call_id: str,
        execution_token: str,
        status: str,
        result: dict[str, Any],
        error_message: str = "",
    ) -> dict[str, Any]:
        """记录工具调用终态。"""


class DatabaseToolInvocationStore:
    """使用当前项目同步 PostgreSQL 客户端调用幂等 RPC。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    def begin(
        self,
        *,
        task_id: str,
        conversation_id: str,
        turn_id: str,
        execution_token: str,
        tool_call_id: str,
        tool_name: str,
        args_hash: str,
    ) -> dict[str, Any]:
        response = self._db.rpc(
            "begin_tool_invocation",
            {
                "p_task_id": task_id,
                "p_conversation_id": conversation_id,
                "p_turn_id": turn_id,
                "p_execution_token": execution_token,
                "p_tool_call_id": tool_call_id,
                "p_tool_name": tool_name,
                "p_args_hash": args_hash,
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_TOOL_INVOCATION_BEGIN_RESULT_INVALID")
        return data

    def mark_stale(
        self,
        *,
        task_id: str,
        turn_id: str,
        tool_call_id: str,
        execution_token: str,
        stale_after_seconds: int = 900,
    ) -> dict[str, Any]:
        response = self._db.rpc(
            "mark_stale_tool_invocation_uncertain",
            {
                "p_task_id": task_id,
                "p_turn_id": turn_id,
                "p_tool_call_id": tool_call_id,
                "p_execution_token": execution_token,
                "p_stale_after_seconds": stale_after_seconds,
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_TOOL_INVOCATION_STALE_RESULT_INVALID")
        return data

    def complete(
        self,
        *,
        task_id: str,
        turn_id: str,
        tool_call_id: str,
        execution_token: str,
        status: str,
        result: dict[str, Any],
        error_message: str = "",
    ) -> dict[str, Any]:
        response = self._db.rpc(
            "complete_tool_invocation",
            {
                "p_task_id": task_id,
                "p_turn_id": turn_id,
                "p_tool_call_id": tool_call_id,
                "p_execution_token": execution_token,
                "p_status": status,
                "p_result": Jsonb(result),
                "p_error_message": error_message,
            },
        ).execute()
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("ACTOR_TOOL_INVOCATION_COMPLETE_RESULT_INVALID")
        return data


def hash_tool_arguments(arguments: dict[str, Any]) -> str:
    """生成稳定参数指纹；只保存 hash，不把原始参数写入幂等表。"""
    encoded = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def serialize_tool_result(result: Any) -> dict[str, Any]:
    """保留可安全回放的最小结果，不复制整个大数据结果。"""
    from services.agent.agent_result import AgentResult

    if isinstance(result, AgentResult):
        return {
            "kind": "agent_result",
            "summary": result.to_tool_content(),
            "status": str(result.status),
            "error_message": result.error_message,
            "emit_payloads": result.emit_payloads,
        }
    if isinstance(result, (str, int, float, bool)) or result is None:
        return {"kind": "scalar", "value": result}
    try:
        json.dumps(result, ensure_ascii=False)
        return {"kind": "json", "value": result}
    except (TypeError, ValueError):
        return {"kind": "scalar", "value": str(result)}


def deserialize_tool_result(payload: Any) -> Any:
    """将幂等表的回放载荷恢复为工具循环可接受的结果。"""
    from services.agent.agent_result import AgentResult

    if not isinstance(payload, dict):
        return str(payload)
    kind = payload.get("kind")
    if kind == "agent_result":
        return AgentResult(
            summary=str(payload.get("summary") or ""),
            status=str(payload.get("status") or "success"),
            error_message=str(payload.get("error_message") or ""),
            emit_payloads=(
                payload.get("emit_payloads")
                if isinstance(payload.get("emit_payloads"), list)
                else []
            ),
        )
    if kind in {"scalar", "json"}:
        return payload.get("value")
    return str(payload)
