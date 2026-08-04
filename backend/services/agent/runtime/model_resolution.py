"""Resolve the user-facing chat model for a Runtime Run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.adapters.factory import DEFAULT_MODEL_ID, get_model_config
from services.agent.runtime.infrastructure.model.projection import (
    resolve_model_revision,
)


@dataclass(frozen=True, kw_only=True)
class RuntimeModelResolution:
    model_id: str
    provider: str
    revision: str
    source: str
    subscription_state: str = "not_used"

    def snapshot(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "revision": self.revision,
            "source": self.source,
            "subscription_state": self.subscription_state,
        }


def resolve_runtime_model(raw_model_id: Any) -> RuntimeModelResolution:
    """Use a valid explicit chat model, otherwise the existing chat default."""
    candidate = raw_model_id.strip() if isinstance(raw_model_id, str) else ""
    if candidate and candidate != "auto" and get_model_config(candidate):
        source = "explicit"
        model_id = candidate
    else:
        source = "default"
        model_id = DEFAULT_MODEL_ID
    config = get_model_config(model_id)
    if config is None:
        raise RuntimeError("RUNTIME_DEFAULT_MODEL_INVALID")
    return RuntimeModelResolution(
        model_id=model_id,
        provider=str(config.provider.value),
        revision=resolve_model_revision(model_id),
        source=source,
    )


def snapshot_from_resolution(
    resolution: RuntimeModelResolution,
) -> dict[str, object]:
    return {
        "resolved_model": resolution.snapshot(),
        "model_id": resolution.model_id,
        "provider": resolution.provider,
        "revision": resolution.revision,
        "model_selection_source": resolution.source,
        "subscription_state": resolution.subscription_state,
    }
