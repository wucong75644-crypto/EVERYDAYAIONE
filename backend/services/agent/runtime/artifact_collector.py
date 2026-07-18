"""把现有工具返回旁路映射为运行时证据。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_ledger import (
    ArtifactEvidence,
    ArtifactKind,
    ArtifactSource,
    ArtifactStatus,
)


_EMIT_KINDS = {
    "chart": ArtifactKind.CHART,
    "diagram": ArtifactKind.DIAGRAM,
    "file": ArtifactKind.FILE,
    "image": ArtifactKind.IMAGE,
    "table": ArtifactKind.TABLE,
    "video": ArtifactKind.VIDEO,
}


def collect_tool_result(
    result: Any,
    *,
    tool_call_id: str | None,
) -> tuple[ArtifactEvidence, ...]:
    """只消费结构化字段；普通字符串和 Markdown 不升级为可信证据。"""
    if not isinstance(result, AgentResult):
        return ()
    status = _artifact_status(result)
    evidence: list[ArtifactEvidence] = []
    if result.data is not None or result.file_ref is not None:
        payload = {
            "summary": result.summary,
            "data": result.data,
            "columns": result.columns,
            "file_ref": result.file_ref,
            "source": result.source,
            "metadata": result.metadata,
        }
        evidence.append(
            _evidence(
                ArtifactKind.DATA_RESULT,
                status,
                payload,
                tool_call_id=tool_call_id,
            )
        )
    for payload in result.emit_payloads:
        kind = _EMIT_KINDS.get(str(payload.get("kind") or ""))
        if kind:
            evidence.append(
                _evidence(
                    kind,
                    status,
                    payload,
                    tool_call_id=tool_call_id,
                    explicit=True,
                    renderer_format=_renderer_format(payload),
                )
            )
            if kind == ArtifactKind.TABLE:
                evidence.append(
                    _evidence(
                        ArtifactKind.DATA_RESULT,
                        status,
                        {
                            "summary": payload.get("title") or result.summary,
                            "data": payload.get("rows"),
                            "columns": _table_columns(payload),
                            "file_ref": None,
                            "source": result.source or "tool_emit_table",
                            "metadata": {
                                "derived_from": result.metadata.get(
                                    "derived_from",
                                    [],
                                ),
                                "sandbox_table": True,
                            },
                        },
                        tool_call_id=tool_call_id,
                    )
                )
    return tuple(evidence)


def _artifact_status(result: AgentResult) -> ArtifactStatus:
    if result.is_failure:
        return ArtifactStatus.FAILED
    if result.status == "empty":
        return ArtifactStatus.EMPTY
    if result.status == "partial":
        return ArtifactStatus.DEGRADED
    return ArtifactStatus.READY


def _evidence(
    kind: ArtifactKind,
    status: ArtifactStatus,
    payload: Any,
    *,
    tool_call_id: str | None,
    explicit: bool = False,
    renderer_format: str | None = None,
) -> ArtifactEvidence:
    return ArtifactEvidence(
        kind=kind,
        source=ArtifactSource.TOOL_RESULT,
        status=status,
        fingerprint=_fingerprint(kind, payload),
        tool_call_id=tool_call_id,
        renderer_format=renderer_format,
        explicit=explicit,
        payload=_normalize(payload),
    )


def _fingerprint(kind: ArtifactKind, payload: Any) -> str:
    raw = json.dumps(
        _normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{kind.value}:{raw}".encode()).hexdigest()


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


def _renderer_format(payload: dict[str, Any]) -> str | None:
    value = payload.get("spec_format") or payload.get("format")
    return str(value) if value else None


def _table_columns(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = payload.get("rows")
    first = rows[0] if isinstance(rows, list) and rows else {}
    columns = payload.get("columns")
    if not isinstance(columns, list):
        return []
    return [
        {
            "name": str(name),
            "label": str(name),
            "dtype": type(first.get(name)).__name__,
        }
        for name in columns
    ]
