"""Shared Runtime ingress for prepared web media tasks."""

from __future__ import annotations

from typing import Any, Mapping


async def submit_runtime_media_ingress(
    *, db: Any, conversation_id: str, user_id: str, org_id: str | None,
    task_id: str, input_message_id: str | None, output_message_id: str,
    turn_id: str | None, idempotency_key: str, kind: str,
    request: Mapping[str, object], model_id: str,
):
    """Submit one prepared task through the narrow Runtime media RPC."""
    from core.config import get_settings
    from services.agent.runtime.media_ingress import (
        RuntimeMediaIngress, RuntimeMediaIngressReceipt,
    )

    settings = get_settings()
    if not all((
        settings.agent_runtime_media_enabled,
        settings.agent_runtime_media_provider_probe_passed,
        settings.agent_runtime_media_production_ready,
    )):
        return RuntimeMediaIngressReceipt(outcome="media_not_ready")
    if not input_message_id:
        raise RuntimeError("RUNTIME_MEDIA_INPUT_MESSAGE_REQUIRED")
    row = db.table("conversations").select(
        "scope_type,scope_id",
    ).eq("id", conversation_id).single().execute()
    conversation = getattr(row, "data", None)
    if not isinstance(conversation, dict):
        raise RuntimeError("RUNTIME_MEDIA_CONVERSATION_MISSING")
    return await RuntimeMediaIngress(db).submit(
        conversation_id=conversation_id, org_id=org_id, user_id=user_id,
        scope_kind=str(conversation["scope_type"]),
        scope_id=str(conversation["scope_id"]),
        agent_definition_id=settings.agent_runtime_agent_definition_id,
        agent_definition_revision=settings.agent_runtime_agent_definition_revision,
        task_id=task_id, input_message_id=input_message_id,
        output_message_id=output_message_id, turn_id=turn_id,
        idempotency_key=idempotency_key, kind=kind, request=request,
        model_id=model_id,
    )
