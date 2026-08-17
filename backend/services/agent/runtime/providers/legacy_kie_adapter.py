"""Runtime bridge to the existing KIE media adapters."""

from __future__ import annotations

from typing import Any

from services.adapters.factory import (
    get_image_model_config, get_video_model_config,
)
from services.adapters.kie import KieClient, KieImageAdapter, KieVideoAdapter


class RuntimeLegacyKieAdapterFactory:
    """Create the already-supported KIE adapters with Runtime credentials."""

    def create(self, kind: str, model_id: str, api_key: str) -> Any:
        if not api_key.strip():
            raise RuntimeError("KIE_CREDENTIAL_UNAVAILABLE")
        if kind == "image":
            config = get_image_model_config(model_id)
            if not config:
                raise ValueError("KIE_IMAGE_MODEL_NOT_REGISTERED")
            return KieImageAdapter(
                KieClient(api_key), config["provider_model"],
            )
        if kind == "video":
            config = get_video_model_config(model_id)
            if not config:
                raise ValueError("KIE_VIDEO_MODEL_NOT_REGISTERED")
            return KieVideoAdapter(
                KieClient(api_key), config["provider_model"],
            )
        raise ValueError("RUNTIME_LEGACY_KIE_MEDIA_KIND_INVALID")


__all__ = ["RuntimeLegacyKieAdapterFactory"]
