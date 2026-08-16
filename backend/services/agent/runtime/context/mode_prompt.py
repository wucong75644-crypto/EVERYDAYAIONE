"""Runtime permission-mode normalization and prompt projection."""

from __future__ import annotations

from typing import Final

from services.prompt_builder.layers.session_stable_layer import (
    SessionStableContext,
    SessionStableLayer,
)
from services.prompt_builder.layers.static_layer import (
    render_permission_mode_section,
)


VALID_PERMISSION_MODES: Final = frozenset({"auto", "ask", "plan"})


def normalize_permission_mode(value: object) -> str:
    """Keep Runtime mode input compatible with the legacy chat boundary."""
    if value is True or value == "true":
        return "plan"
    if value is False or value == "false" or value is None:
        return "auto"
    mode = str(value).strip().lower()
    return mode if mode in VALID_PERMISSION_MODES else "auto"


def render_runtime_mode_prompt(value: object) -> str:
    """Render shared mode rules plus the current mode for one Run."""
    mode = normalize_permission_mode(value)
    current_mode = SessionStableLayer.render(
        SessionStableContext(permission_mode=mode),
    )
    return "\n\n".join((render_permission_mode_section(), current_mode))


__all__ = [
    "VALID_PERMISSION_MODES",
    "normalize_permission_mode",
    "render_runtime_mode_prompt",
]
