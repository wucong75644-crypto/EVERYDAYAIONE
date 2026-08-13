"""Runtime media slot retry adapter for the unified message endpoint."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from api.deps import OrgCtx, ScopedDB
from core.exceptions import AppException
from schemas.message import GenerateRequest, GenerateResponse, GenerationType, Message, MessageOperation
from services.runtime_media_message_control import RuntimeMediaMessageControlService
from services.user_activity_service import record_user_activity


async def try_runtime_media_slot_retry(
    *, conversation_id: str, body: GenerateRequest, ctx: OrgCtx,
    db: ScopedDB, user_id: str, request_id: str, gen_type: GenerationType,
    record_feedback: Callable[[Any, str, GenerateRequest, GenerationType, str], None],
) -> GenerateResponse | None:
    """Route only ordinary-chat Runtime image slots to the 228.07 control."""
    if gen_type != GenerationType.IMAGE or body.operation != MessageOperation.REGENERATE_SINGLE:
        return None
    if not body.original_message_id:
        return None
    response = db.table("messages").select("*").eq(
        "id", body.original_message_id,
    ).eq("conversation_id", conversation_id).maybe_single().execute()
    row = getattr(response, "data", None)
    if not isinstance(row, dict):
        raise AppException(
            code="RUNTIME_MEDIA_ORIGINAL_MESSAGE_NOT_FOUND",
            message="未找到可重试的图片消息",
            status_code=404,
        )
    generation_params = _json_object(row.get("generation_params"))
    if not isinstance(generation_params.get("runtime_media_batch"), dict):
        return None
    params = body.params or {}
    slot_index = params.get("image_index")
    if isinstance(slot_index, bool) or not isinstance(slot_index, int) or not 0 <= slot_index <= 9:
        raise AppException(code="RUNTIME_MEDIA_SLOT_INDEX_INVALID", message="图片位置无效，请刷新后重试", status_code=422)
    slot = _runtime_image_slot(row.get("content"), slot_index)
    if slot is None:
        raise AppException(code="RUNTIME_MEDIA_SLOT_NOT_FOUND", message="未找到可重试的图片位置", status_code=404)
    if params.get("runtime_slot_id") not in (None, slot[0]) or params.get("runtime_slot_revision") not in (None, slot[1]):
        raise AppException(code="RUNTIME_MEDIA_SLOT_STALE", message="图片状态已变化，请刷新后重试", status_code=409)
    receipt = await RuntimeMediaMessageControlService(
        db, user_id=user_id, org_id=ctx.org_id,
    ).retry_slot(
        body.original_message_id, conversation_id, slot_index,
        slot_id=slot[0], expected_slot_revision=slot[1],
        idempotency_key=f"runtime-media-retry:{request_id}",
        client_task_id=body.client_task_id,
        task_slot_id=(str(params["_task_slot_id"]) if params.get("_task_slot_id") is not None else None),
    )
    if receipt is None:
        raise AppException(code="RUNTIME_MEDIA_RETRY_BINDING_MISSING", message="Runtime 图片任务不可重试", status_code=409)
    refreshed = db.table("messages").select("*").eq(
        "id", body.original_message_id,
    ).eq("conversation_id", conversation_id).single().execute()
    assistant = Message.model_validate(refreshed.data).model_copy(update={"task_id": receipt.task_id})
    record_feedback(db, user_id, body, gen_type, conversation_id)
    record_user_activity(
        db, user_id=user_id, event_type="task_created", org_id=ctx.org_id,
        source="web", resource_type="task", resource_id=receipt.task_id,
        metadata={"conversation_id": conversation_id, "generation_type": gen_type.value,
                  "operation": body.operation.value, "slot_index": slot_index},
    )
    return GenerateResponse(
        task_id=body.client_task_id or receipt.task_id,
        user_message=None, assistant_message=assistant,
        operation=body.operation, generation_type=gen_type.value,
    )


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
    return {}


def _runtime_image_slot(content: object, slot_index: int) -> tuple[str, int] | None:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(content, list):
        return None
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image" and part.get("slot_index") == slot_index:
            slot_id, revision = part.get("slot_id"), part.get("slot_revision")
            if isinstance(slot_id, str) and isinstance(revision, int) and not isinstance(revision, bool):
                return slot_id, revision
    return None
