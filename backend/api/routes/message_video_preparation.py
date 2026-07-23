"""Web 视频任务的统一生成事务准备与供应商启动。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from schemas.message import (
    GenerateRequest, GenerateResponse, GenerationParams, GenerationType,
    Message, MessageOperation, MessageRole, MessageStatus,
)
from services.generation_lifecycle import GenerationLifecycle
from services.handlers.base import TaskMetadata
from services.handlers.video_prepared_submission import resolve_video_submission_settings
from services.user_activity_service import record_user_activity


_VIDEO_TURN_NAMESPACE = uuid.UUID("03a0586f-21b1-4a9d-9870-d4ab734ebac2")
_VIDEO_INPUT_NAMESPACE = uuid.UUID("5acb9221-ddab-4dbc-b22c-34179699f4b2")
_VIDEO_TASK_NAMESPACE = uuid.UUID("81a55bef-5dc8-4712-83ba-dc5d2c50997a")


@dataclass
class PreparedVideoTaskMetadata(TaskMetadata):
    """VideoHandler 消费的已准备本地 task。"""

    prepared_task_id: str | None = None


async def prepare_and_start_video_generation(
    *, db: Any, handler: Any, conversation_service: Any,
    conversation_id: str, user_id: str, org_id: str | None,
    request_id: str, body: GenerateRequest,
) -> GenerateResponse:
    """原子准备视频消息与本地 task，再提交视频供应商。"""
    conversation = await conversation_service.get_conversation(
        conversation_id, user_id, org_id,
    )
    if body.params is None:
        body.params = {}
    body.params["_org_id"] = org_id
    params = _business_params(body)
    settings = resolve_video_submission_settings(handler, body.content, params)
    handler._check_balance(user_id, settings.credits)
    existing_output = body.operation in {
        MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE,
    }
    turn_id = None if existing_output else _stable_id(_VIDEO_TURN_NAMESPACE, request_id)
    input_id = None if existing_output else _stable_id(_VIDEO_INPUT_NAMESPACE, request_id)
    task_id = _stable_id(_VIDEO_TASK_NAMESPACE, request_id)
    created_at = body.created_at or datetime.now(timezone.utc)
    placeholder_at = body.placeholder_created_at or datetime.now(timezone.utc)
    request_params = {
        "prompt": settings.prompt, "model": settings.model_id,
        **handler._serialize_params(params),
    }
    preparation = GenerationLifecycle(db).prepare(
        request_id=request_id, operation=body.operation.value,
        conversation_id=conversation_id, user_id=user_id, org_id=org_id,
        turn_id=turn_id,
        input_message=_input_payload(body, input_id, created_at),
        output_message=_output_payload(body, placeholder_at),
        tasks=[{
            "id": task_id, "client_task_id": body.client_task_id,
            "user_id": user_id, "org_id": org_id,
            "conversation_id": conversation_id, "type": "video",
            "status": "preparing", "model_id": settings.model_id,
            "request_params": request_params,
            "placeholder_created_at": placeholder_at.isoformat(),
            "execution_mode": "serial", "delivery_context": {"channel": "web"},
        }],
    )
    metadata = PreparedVideoTaskMetadata(
        client_task_id=_required(body.client_task_id),
        placeholder_created_at=placeholder_at,
        input_message_id=preparation.input_message_id,
        turn_id=preparation.turn_id,
        context_anchor=preparation.context_anchor(task_id, org_id),
        prepared_task_id=task_id,
    )
    external_task_id = await handler.start(
        message_id=preparation.output_message_id,
        conversation_id=conversation_id, user_id=user_id,
        content=body.content, params=params, metadata=metadata,
    )
    user_message = _user_message(
        body, conversation_id, preparation.input_message_id,
        preparation.turn_id, created_at,
    )
    if user_message:
        record_user_activity(
            db, user_id=user_id, event_type="message_sent", org_id=org_id,
            source="web", resource_type="message", resource_id=user_message.id,
            metadata={"conversation_id": conversation_id},
        )
    record_user_activity(
        db, user_id=user_id, event_type="task_created", org_id=org_id,
        source="web", resource_type="task", resource_id=external_task_id,
        metadata={
            "conversation_id": conversation_id, "generation_type": "video",
            "operation": body.operation.value,
        },
    )
    return GenerateResponse(
        task_id=_required(body.client_task_id), user_message=user_message,
        assistant_message=_assistant_message(
            body, conversation_id, preparation.output_message_id,
            preparation.input_message_id, preparation.turn_id, placeholder_at,
        ),
        operation=body.operation, generation_type=GenerationType.VIDEO.value,
    )


def _business_params(body: GenerateRequest) -> dict[str, Any]:
    params = {
        key: value for key, value in (body.params or {}).items()
        if key not in {"client_task_id", "placeholder_created_at", "_org_id"}
    }
    if body.model:
        params["model"] = body.model
    params["operation"] = body.operation.value
    return params


def _input_payload(
    body: GenerateRequest, message_id: str | None, created_at: datetime,
) -> dict[str, Any]:
    if message_id is None:
        return {}
    return {
        "id": message_id,
        "content": [part.model_dump(mode="json") for part in body.content],
        "client_request_id": body.client_request_id,
        "created_at": created_at.isoformat(),
    }


def _generation_params(body: GenerateRequest) -> dict[str, Any]:
    result: dict[str, Any] = {"type": GenerationType.VIDEO.value}
    if body.model:
        result["model"] = body.model
    for key in ("aspect_ratio", "n_frames", "remove_watermark", "_render"):
        if key in (body.params or {}):
            result[key] = body.params[key]
    return result


def _output_payload(body: GenerateRequest, created_at: datetime) -> dict[str, Any]:
    render = (body.params or {}).get("_render", {})
    text = render.get("placeholder_text") or "视频生成中"
    return {
        "id": _required(body.assistant_message_id),
        "content": [{"type": "text", "text": text}],
        "status": MessageStatus.PENDING.value,
        "generation_params": _generation_params(body),
        "created_at": created_at.isoformat(),
    }


def _user_message(
    body: GenerateRequest, conversation_id: str, message_id: str,
    turn_id: str, created_at: datetime,
) -> Message | None:
    if body.operation in {MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE}:
        return None
    return Message(
        id=message_id, conversation_id=conversation_id, role=MessageRole.USER,
        content=body.content, status=MessageStatus.COMPLETED, created_at=created_at,
        client_request_id=body.client_request_id, turn_id=turn_id,
    )


def _assistant_message(
    body: GenerateRequest, conversation_id: str, message_id: str,
    input_id: str, turn_id: str, created_at: datetime,
) -> Message:
    return Message(
        id=message_id, conversation_id=conversation_id,
        role=MessageRole.ASSISTANT, content=[], status=MessageStatus.PENDING,
        created_at=created_at, task_id=body.client_task_id,
        generation_params=GenerationParams(**_generation_params(body)),
        turn_id=turn_id, reply_to_message_id=input_id,
    )


def _stable_id(namespace: uuid.UUID, value: str) -> str:
    return str(uuid.uuid5(namespace, value))


def _required(value: str | None) -> str:
    if not value:
        raise RuntimeError("GENERATION_REQUEST_IDENTITY_MISSING")
    return value
