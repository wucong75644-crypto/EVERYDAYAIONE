"""Shared Runtime ingress for prepared web media tasks."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.agent.runtime.catalog.image_release import IMAGE_DEFINITION_REVISION


async def submit_runtime_media_ingress(
    *, db: Any, conversation_id: str, user_id: str, org_id: str | None,
    task_id: str, input_message_id: str | None, output_message_id: str,
    turn_id: str | None, idempotency_key: str, kind: str,
    request: Mapping[str, object], model_id: str,
):
    """Submit one prepared task through the narrow Runtime media RPC."""
    from core.config import get_settings
    from services.agent.runtime.media_ingress import RuntimeMediaIngress

    settings = get_settings()
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
        # Image ingress has its own frozen v13 media definition. Video keeps
        # the existing revision until its own Runtime release is approved.
        agent_definition_revision=(
            IMAGE_DEFINITION_REVISION if kind == "image"
            else settings.agent_runtime_agent_definition_revision
        ),
        task_id=task_id, input_message_id=input_message_id,
        output_message_id=output_message_id, turn_id=turn_id,
        idempotency_key=idempotency_key, kind=kind, request=request,
        model_id=model_id,
    )


async def submit_runtime_image_batch_ingress(
    *, db: Any, conversation_id: str, user_id: str, org_id: str | None,
    input_message_id: str | None, output_message_id: str,
    turn_id: str | None, batch_id: str, model_id: str,
    items: Sequence[Mapping[str, object]],
):
    """Atomically submit one prepared ordinary-image batch to Runtime."""
    from core.config import get_settings
    from services.agent.runtime.media_ingress import RuntimeMediaIngress

    settings = get_settings()
    if not input_message_id:
        raise RuntimeError("RUNTIME_MEDIA_INPUT_MESSAGE_REQUIRED")
    row = db.table("conversations").select(
        "scope_type,scope_id",
    ).eq("id", conversation_id).single().execute()
    conversation = getattr(row, "data", None)
    if not isinstance(conversation, dict):
        raise RuntimeError("RUNTIME_MEDIA_CONVERSATION_MISSING")
    return await RuntimeMediaIngress(db).submit_image_batch(
        conversation_id=conversation_id, org_id=org_id, user_id=user_id,
        scope_kind=str(conversation["scope_type"]),
        scope_id=str(conversation["scope_id"]),
        agent_definition_id=settings.agent_runtime_agent_definition_id,
        agent_definition_revision=IMAGE_DEFINITION_REVISION,
        input_message_id=input_message_id, output_message_id=output_message_id,
        turn_id=turn_id, batch_id=batch_id, model_id=model_id, items=items,
    )
