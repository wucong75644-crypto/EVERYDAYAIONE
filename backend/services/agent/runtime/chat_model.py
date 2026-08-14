"""Runtime-owned model client boundary for conversational execution."""

from __future__ import annotations

from typing import Any


def create_runtime_chat_model(
    model_id: str, *, org_id: str | None, db: Any,
) -> Any:
    """Resolve the model through the Runtime provider boundary."""
    from services.adapters.factory import create_chat_adapter

    return create_chat_adapter(model_id, org_id=org_id, db=db)
