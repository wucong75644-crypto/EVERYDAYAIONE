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


@dataclass(frozen=True)
class DataContextSnapshot:
    evidence: tuple[ArtifactEvidence, ...] = ()

    def render_prompt(self) -> str:
        entries: list[str] = []
        for item in self.evidence:
            payload = item.payload or {}
            columns = payload.get("columns")
            names = [
                str(column.get("name"))
                for column in columns
                if isinstance(column, dict) and column.get("name")
            ] if isinstance(columns, list) else []
            rows = payload.get("data")
            count = len(rows) if isinstance(rows, list) else "file"
            entries.append(
                f"- artifact_id={item.fingerprint}; rows={count}; "
                f"columns={','.join(names)}"
            )
        if not entries:
            return ""
        return (
            "[历史可信数据证据]\n"
            + "\n".join(entries)
            + "\n当用户要求排除、切换指标、求和、计数、分组或重新计算时，"
            "必须调用 data_compute；不得从历史回答文本手工计算。"
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
            "metric_definitions,lineage,validation_status,context_revision"
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
            "metadata": metadata,
        },
    )
