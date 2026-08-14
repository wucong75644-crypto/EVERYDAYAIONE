"""Content-addressed Artifact materialization with partial isolation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class MaterializeCheckpoint:
    content_hash: str
    revision: int
    status: str
    byte_size: int


class ArtifactMaterializer:
    """Small pure boundary; persistence is supplied by the caller/RPC port."""

    def checkpoint(self, content: bytes, *, revision: int = 1, partial: bool = False) -> MaterializeCheckpoint:
        if revision < 1:
            raise ValueError("ARTIFACT_REVISION_INVALID")
        return MaterializeCheckpoint(
            content_hash=hashlib.sha256(content).hexdigest(),
            revision=revision,
            status="partial" if partial else "materialized",
            byte_size=len(content),
        )
    def retry_materialize(self, checkpoint: MaterializeCheckpoint) -> MaterializeCheckpoint:
        if checkpoint.status != "materialize_failed":
            raise ValueError("ARTIFACT_RETRY_MATERIALIZE_ONLY")
        return MaterializeCheckpoint(
            content_hash=checkpoint.content_hash,
            revision=checkpoint.revision + 1,
            status="materialized",
            byte_size=checkpoint.byte_size,
        )


__all__ = ["ArtifactMaterializer", "MaterializeCheckpoint"]
