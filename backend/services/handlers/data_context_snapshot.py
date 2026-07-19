"""固定 revision 的跨 Turn 数据证据快照。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.agent.runtime.artifact_ledger import (
    ArtifactEvidence,
    ArtifactKind,
    ArtifactSource,
    ArtifactStatus,
)
from services.agent.runtime.context.providers.evidence import (
    build_evidence_model_view,
    render_evidence_model_view,
)


@dataclass(frozen=True)
class DataContextSnapshot:
    evidence: tuple[ArtifactEvidence, ...] = ()

    def render_prompt(self) -> str:
        """向模型提供最近的有界可信数据视图。"""
        entries: list[str] = []
        for item in self.evidence[:5]:
            payload = item.payload or {}
            model_view = payload.get("model_view")
            if not isinstance(model_view, dict):
                metadata = payload.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                model_view = build_evidence_model_view(
                    artifact_id=item.fingerprint,
                    source=str(payload.get("source") or ""),
                    rows=payload.get("data"),
                    columns=payload.get("columns"),
                    file_ref=payload.get("file_ref"),
                    query_scope=metadata.get("query_scope"),
                    metric_definitions=metadata.get("metric_definitions"),
                ).model_view
            entries.append(
                f"- {render_evidence_model_view(model_view)}"
            )
        if not entries:
            return ""
        return (
            "[历史可信数据证据]\n"
            + "\n".join(entries)
            + "\n这些视图可直接用于连续追问和确定性计算；"
            "用户要求最新数据、视图不含所需行或证据不足时，再调用数据源工具。"
        )


def load_data_context_snapshot(
    db: Any,
    *,
    conversation_id: str,
    base_revision: int,
) -> DataContextSnapshot:
    result = (
        db.table("conversation_data_evidence")
        .select(
            "artifact_id,source,columns,rows,file_ref,query_scope,"
            "metric_definitions,lineage,validation_status,context_revision,"
            "model_view,content_hash,byte_size,expires_at"
        )
        .eq("conversation_id", conversation_id)
        .lte("context_revision", base_revision)
        .order("context_revision", desc=True)
        .range(0, 49)
        .execute()
    )
    rows = result.data if result and isinstance(result.data, list) else []
    evidence: list[ArtifactEvidence] = []
    seen: set[str] = set()
    for row in rows:
        artifact_id = str(row.get("artifact_id") or "")
        if not artifact_id or artifact_id in seen:
            continue
        if row.get("validation_status") != "ready":
            continue
        seen.add(artifact_id)
        evidence.append(_to_evidence(row, artifact_id))
    return DataContextSnapshot(tuple(evidence))


def _to_evidence(row: dict[str, Any], artifact_id: str) -> ArtifactEvidence:
    lineage = row.get("lineage")
    lineage = lineage if isinstance(lineage, dict) else {}
    metadata = {
        "query_scope": row.get("query_scope") or {},
        "metric_definitions": row.get("metric_definitions") or {},
        "derived_from": lineage.get("derived_from") or [],
        "operation": lineage.get("operation") or {},
        "persisted": True,
    }
    return ArtifactEvidence(
        kind=ArtifactKind.DATA_RESULT,
        source=ArtifactSource.TOOL_RESULT,
        status=ArtifactStatus.READY,
        fingerprint=artifact_id,
        tool_call_id=lineage.get("tool_call_id"),
        payload={
            "data": row.get("rows"),
            "columns": row.get("columns") or [],
            "file_ref": row.get("file_ref"),
            "source": row.get("source") or "",
            "model_view": row.get("model_view"),
            "content_hash": row.get("content_hash"),
            "byte_size": row.get("byte_size"),
            "expires_at": row.get("expires_at"),
            "metadata": metadata,
        },
    )
