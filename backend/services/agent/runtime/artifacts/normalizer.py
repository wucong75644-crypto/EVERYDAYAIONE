"""把任意工具返回值规范化为完整 ArtifactDraft。"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from services.agent.agent_result import AgentResult

from .types import ArtifactDraft


_NAMESPACE = uuid.UUID("da75bf92-94dd-40d4-82d7-3ef364080826")
_MODEL_VIEW_BYTES = 40 * 1024
_HISTORY_VIEW_BYTES = 8 * 1024


def normalize_tool_result(
    result: Any,
    *,
    tool_call_id: str,
    tool_name: str,
    is_error: bool = False,
    conversation_id: str = "",
) -> ArtifactDraft:
    """规范化任意工具结果，不按工具名改变保留策略。"""
    content, artifact_type, status, metadata = _extract_result(
        result, is_error=is_error
    )
    encoded = _encode(content)
    digest = hashlib.sha256(encoded).hexdigest()
    artifact_id = str(uuid.uuid5(
        _NAMESPACE,
        f"{conversation_id}:{tool_call_id}:{artifact_type}:{digest}",
    ))
    return ArtifactDraft(
        artifact_id=artifact_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        artifact_type=artifact_type,
        status=status,
        content=content,
        content_hash=digest,
        byte_size=len(encoded),
        model_view=_bounded_view(content, encoded, _MODEL_VIEW_BYTES),
        history_view=_bounded_view(content, encoded, _HISTORY_VIEW_BYTES),
        metadata=metadata,
    )


def canonical_json(value: Any) -> str:
    """生成稳定、可哈希的 JSON。"""
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _extract_result(
    result: Any, *, is_error: bool
) -> tuple[Any, str, str, dict[str, Any]]:
    if isinstance(result, AgentResult):
        content = {
            "summary": result.summary,
            "status": result.status,
            "format": _normalize(result.format),
            "data": _normalize(result.data),
            "columns": _normalize(result.columns),
            "file_ref": _normalize(result.file_ref),
            "source": result.source,
            "error_message": result.error_message,
            "metadata": _normalize(result.metadata),
            "emit_payloads": _normalize(result.emit_payloads),
            "insights": _normalize(result.insights),
            "follow_up": _normalize(result.follow_up),
        }
        artifact_type = _agent_result_type(result)
        status = "failed" if result.is_failure or is_error else "ready"
        return content, artifact_type, status, {
            "source": result.source,
            "result_status": str(result.status),
        }
    content = _normalize(result)
    artifact_type = _infer_type(content, is_error=is_error)
    return content, artifact_type, "failed" if is_error else "ready", {}


def _agent_result_type(result: AgentResult) -> str:
    if result.is_failure:
        return "error"
    if result.file_ref:
        return "file"
    if result.data is not None:
        return "table"
    kinds = {
        str(payload.get("kind") or "")
        for payload in result.emit_payloads
        if isinstance(payload, dict)
    }
    if len(kinds) == 1 and next(iter(kinds)) in {"file", "image", "table"}:
        return next(iter(kinds))
    return "mixed" if kinds else "text"


def _infer_type(content: Any, *, is_error: bool) -> str:
    if is_error:
        return "error"
    if isinstance(content, str):
        return "text"
    if isinstance(content, (dict, list)):
        return "json"
    return "text"


def _bounded_view(
    content: Any, encoded: bytes, max_bytes: int
) -> dict[str, Any]:
    if len(encoded) <= max_bytes:
        return {"content": content, "truncated": False}
    head_bytes = int(max_bytes * 0.75)
    tail_bytes = max_bytes - head_bytes
    return {
        "preview_head": encoded[:head_bytes].decode(
            "utf-8", errors="ignore"
        ),
        "preview_tail": encoded[-tail_bytes:].decode(
            "utf-8", errors="ignore"
        ),
        "truncated": True,
        "byte_size": len(encoded),
    }


def _encode(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _normalize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (date, datetime, UUID)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)
