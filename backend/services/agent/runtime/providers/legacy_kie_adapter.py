"""Runtime bridge to the existing KIE media adapters."""

from __future__ import annotations

from typing import Any

from services.adapters.factory import create_image_adapter, create_video_adapter


class RuntimeLegacyKieAdapterFactory:
    """Create media adapters through the unchanged legacy factory path."""

    def create(
        self, kind: str, model_id: str, api_key: str | None = None,
    ) -> Any:
        del api_key
        if kind == "image":
            return create_image_adapter(model_id)
        if kind == "video":
            return create_video_adapter(model_id)
        raise ValueError("RUNTIME_LEGACY_KIE_MEDIA_KIND_INVALID")


__all__ = ["RuntimeLegacyKieAdapterFactory"]
