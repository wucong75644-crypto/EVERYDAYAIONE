"""Run 内产物证据和幂等账本。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ArtifactKind(StrEnum):
    TEXT = "text"
    TABLE = "table"
    CHART = "chart"
    DIAGRAM = "diagram"
    FILE = "file"
    IMAGE = "image"
    VIDEO = "video"
    DATA_RESULT = "data_result"


class ArtifactStatus(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    FAILED = "failed"
    DEGRADED = "degraded"


class ArtifactSource(StrEnum):
    TOOL_RESULT = "tool_result"
    EMIT_PAYLOAD = "emit_payload"


@dataclass(frozen=True)
class ArtifactEvidence:
    kind: ArtifactKind
    source: ArtifactSource
    status: ArtifactStatus
    fingerprint: str
    tool_call_id: str | None = None
    renderer_format: str | None = None
    explicit: bool = False
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ArtifactSnapshot:
    evidence: tuple[ArtifactEvidence, ...]

    @property
    def ready_kinds(self) -> frozenset[ArtifactKind]:
        return frozenset(
            item.kind
            for item in self.evidence
            if item.status == ArtifactStatus.READY
        )


class ArtifactLedger:
    """按 fingerprint 幂等记录产物，保留首次观测顺序。"""

    def __init__(self) -> None:
        self._evidence: dict[str, ArtifactEvidence] = {}

    def record(self, evidence: ArtifactEvidence) -> bool:
        if evidence.fingerprint in self._evidence:
            return False
        self._evidence[evidence.fingerprint] = evidence
        return True

    def snapshot(self) -> ArtifactSnapshot:
        return ArtifactSnapshot(tuple(self._evidence.values()))

    def ready_kinds(self) -> frozenset[ArtifactKind]:
        return self.snapshot().ready_kinds

    def has_explicit(self, kind: ArtifactKind) -> bool:
        return any(
            item.kind == kind and item.explicit
            for item in self._evidence.values()
        )
