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

    def lookup(self, *, task_id, conversation_id, turn_id, tool_call_id):
        """Read an existing record after current access checks; never register IO."""
        response = (self._db.table("tool_invocations")
                    .select("tool_name,args_hash,status,result")
                    .eq("task_id", task_id).eq("conversation_id", conversation_id)
                    .eq("turn_id", turn_id).eq("tool_call_id", tool_call_id)
                    .maybe_single().execute())
        data = response.data if response else None
        if data is not None and not isinstance(data, dict):
            raise RuntimeError("ACTOR_TOOL_INVOCATION_LOOKUP_INVALID")
        return data

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
    from services.tools.result import ToolResult

    if isinstance(result, ToolResult):
        from core.config import get_settings
        from services.tools.result_payload import encode_result, decode_raw
        payload = encode_result(result)
        if get_settings().tool_result_payload_write_version == 1:
            return payload
        # Apply the same safety boundary in the reader-first release, while
        # retaining the legacy outer format and projections for rollback readers.
        result = (result.legacy_persistence_value() if result.exception is not None
                  else decode_raw(payload["tool_result"]))

    from schemas.multimodal import FileReadResult
    from services.scheduler.chat_task_manager import FormBlockResult
    from services.tools.result_payload import (
        _JSONBoundary, _agent_value, _record, decode_raw, validate_payload,
    )
    if isinstance(result, AgentResult):
        # The raw compatibility API has the same safety rules: no runtime handles
        # may leak through AgentResult.to_tool_content or default=str.
        safe = decode_raw({"kind": "agent", "raw": _agent_value(result, _JSONBoundary())})
        return validate_payload({
            "kind": "agent_result",
            "summary": safe.to_tool_content(),
            "status": str(safe.status),
            "error_message": safe.error_message,
            "emit_payloads": safe.emit_payloads,
        })
    if isinstance(result, (str, int, float, bool)) or result is None:
        return validate_payload({"kind": "scalar", "value": result})
    if isinstance(result, (FileReadResult, FormBlockResult)):
        # Preserve the old string projection only for these known value objects,
        # after checking every field. Never stringify an arbitrary DB/lock/error.
        _JSONBoundary().copy(_record(result))
        return validate_payload({"kind": "scalar", "value": str(result)})
    return validate_payload({"kind": "json", "value": result})


def deserialize_tool_result(payload: Any) -> Any:
    """将幂等表的回放载荷恢复为工具循环可接受的结果。"""
    from services.agent.agent_result import AgentResult

    from services.tools.result_payload import extension_of, decode_raw
    extension = extension_of(payload)
    if extension is not None:
        # Full execution/audit restoration requires the currently authorized call.
        # Actor uses restore_result; the legacy API continues to return raw values.
        if extension["kind"] == "exception":
            return AgentResult(summary=extension["error"]["message"], status="error",
                               error_message=extension["error"]["message"])
        return decode_raw(extension)
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
