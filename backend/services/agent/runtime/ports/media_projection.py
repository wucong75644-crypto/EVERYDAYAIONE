"""Narrow ports used by the Runtime media Projection owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True, kw_only=True)
class MediaProjectionAssetRequest:
    """Authoritative facts needed to persist one completed media slot."""

    action_id: str
    slot_id: str
    slot_index: int
    source_url: str
    user_id: str
    org_id: str | None
    conversation_id: str
    message_id: str
    task_id: str
    model_id: str | None
    prompt: str
    aspect_ratio: str
    resolution: str | None

    @property
    def identity(self) -> str:
        """Stable identity shared by workspace file and asset reference."""
        return f"runtime-media:{self.action_id}:{self.slot_id}"


class MediaPersistencePort(Protocol):
    """Persist one provider URL and register its canonical asset."""

    async def persist(
        self, request: MediaProjectionAssetRequest,
    ) -> Mapping[str, object]:
        """Return a content part carrying ``source_url`` and persisted ``url``."""


class ProjectionNotifierPort(Protocol):
    """Best-effort post-commit Projection notification sink."""

    async def notify(self, payload: Mapping[str, object]) -> None:
        """Notify clients after the authoritative DB transaction commits."""


__all__ = [
    "MediaPersistencePort", "MediaProjectionAssetRequest",
    "ProjectionNotifierPort",
]
