"""结构化 Evidence 到模型安全视图的确定性投影。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


_FULL_VIEW_MAX_BYTES = 8 * 1024
_SAMPLED_VIEW_MAX_BYTES = 64 * 1024
_SAMPLE_EDGE_ROWS = 3


@dataclass(frozen=True)
class EvidenceModelProjection:
    """Evidence 持久化和模型消费共用的投影结果。"""

    model_view: dict[str, Any]
    content_hash: str
    byte_size: int


def build_evidence_model_view(
    *,
    artifact_id: str,
    source: str,
    rows: Any,
    columns: Any,
    file_ref: Any,
    query_scope: Any,
    metric_definitions: Any,
) -> EvidenceModelProjection:
    """按 8KB/64KB 阈值生成有界、可复现的模型视图。"""
    normalized_rows = rows if isinstance(rows, list) else None
    normalized_columns = columns if isinstance(columns, list) else []
    normalized_scope = query_scope if isinstance(query_scope, dict) else {}
    normalized_metrics = (
        metric_definitions if isinstance(metric_definitions, dict) else {}
    )
    canonical = {
        "source": source,
        "columns": normalized_columns,
        "rows": normalized_rows,
        "file_ref": file_ref,
        "query_scope": normalized_scope,
        "metric_definitions": normalized_metrics,
    }
    encoded = _canonical_json(canonical).encode("utf-8")
    byte_size = len(encoded)
    common = {
        "artifact_id": artifact_id,
        "source": source,
        "row_count": (
            len(normalized_rows) if normalized_rows is not None else None
        ),
        "columns": normalized_columns,
        "query_scope": normalized_scope,
        "metric_definitions": normalized_metrics,
        "file_ref": file_ref,
        "byte_size": byte_size,
    }
    if normalized_rows is None:
        model_view = {**common, "tier": "reference"}
    elif byte_size <= _FULL_VIEW_MAX_BYTES:
        model_view = {**common, "tier": "full", "rows": normalized_rows}
    elif byte_size <= _SAMPLED_VIEW_MAX_BYTES:
        model_view = {
            **common,
            "tier": "sampled",
            "sample_rows": _edge_sample(normalized_rows),
        }
    else:
        model_view = {**common, "tier": "metadata"}
    return EvidenceModelProjection(
        model_view=model_view,
        content_hash=hashlib.sha256(encoded).hexdigest(),
        byte_size=byte_size,
    )


def render_evidence_model_view(model_view: Mapping[str, Any]) -> str:
    """将受控 model_view 序列化为稳定 Prompt 文本。"""
    return _canonical_json(dict(model_view))


def _edge_sample(rows: list[Any]) -> list[Any]:
    if len(rows) <= _SAMPLE_EDGE_ROWS * 2:
        return list(rows)
    return rows[:_SAMPLE_EDGE_ROWS] + rows[-_SAMPLE_EDGE_ROWS:]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
