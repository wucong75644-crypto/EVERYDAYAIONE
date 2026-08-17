"""Runtime bridge to the existing KIE media adapters."""

from __future__ import annotations

from typing import Any

from services.adapters.kie import KieClient, KieImageAdapter, KieVideoAdapter


class RuntimeLegacyKieAdapterFactory:
    """Create media adapters through the unchanged legacy factory path."""

    def create(
        self, kind: str, model_id: str, api_key: str | None = None,
    ) -> Any:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("RUNTIME_LEGACY_KIE_API_KEY_REQUIRED")
        if kind == "image":
            return KieImageAdapter(KieClient(api_key), model_id)
        if kind == "video":
            return KieVideoAdapter(KieClient(api_key), model_id)
        raise ValueError("RUNTIME_LEGACY_KIE_MEDIA_KIND_INVALID")


__all__ = ["RuntimeLegacyKieAdapterFactory"]
