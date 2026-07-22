"""Web Chat 的统一生成事务准备与 Actor 启动。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from schemas.message import (
    GenerateRequest,
    GenerateResponse,
    GenerationParams,
    GenerationType,
    Message,
    MessageOperation,
    MessageRole,
    MessageStatus,
)
from services.generation_lifecycle import GenerationLifecycle
from services.adapters.factory import DEFAULT_MODEL_ID
from services.handlers.base import TaskMetadata
from services.handlers.chat.actor_enqueue import stable_actor_task_id
from services.user_activity_service import record_user_activity


_CHAT_TURN_NAMESPACE = uuid.UUID("0d4c013c-e500-4ce8-9d88-e69021845ae9")
_CHAT_INPUT_NAMESPACE = uuid.UUID("39731c36-e677-4904-adb1-d75c8e99ecae")


async def prepare_and_start_chat_generation(
    *,
    db: Any,
    handler: Any,
    conversation_id: str,
    user_id: str,
    org_id: str | None,
    request_id: str,
    body: GenerateRequest,
) -> GenerateResponse:
    """原子准备 Chat 消息/task，并使用权威锚点入队 Actor。"""
    client_task_id = _required_identity(body.client_task_id)
    assistant_message_id = _required_identity(body.assistant_message_id)
    created_at = body.created_at or datetime.now(timezone.utc)
    placeholder_at = body.placeholder_created_at or datetime.now(timezone.utc)
    is_existing_output = body.operation in {
        MessageOperation.RETRY,
        MessageOperation.REGENERATE_SINGLE,
    }
    input_message_id = None if is_existing_output else _stable_id(
        _CHAT_INPUT_NAMESPACE, request_id
    )
    turn_id = None if is_existing_output else _stable_id(_CHAT_TURN_NAMESPACE, request_id)
    internal_task_id = stable_actor_task_id(
        user_id=user_id,
        conversation_id=conversation_id,
        external_task_id=client_task_id,
    )
    business_params = _business_params(body)
    task_payload = _build_task_payload(
        handler=handler,
        body=body,
        business_params=business_params,
        internal_task_id=internal_task_id,
        client_task_id=client_task_id,
        assistant_message_id=assistant_message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        placeholder_at=placeholder_at,
    )
    preparation = GenerationLifecycle(db).prepare(
        request_id=request_id,
        operation=body.operation.value,
        conversation_id=conversation_id,
        user_id=user_id,
        org_id=org_id,
        turn_id=turn_id,
        input_message=_input_payload(body, input_message_id, created_at),
        output_message=_output_payload(body, assistant_message_id, placeholder_at),
        tasks=[task_payload],
    )
    metadata = TaskMetadata(
        client_task_id=client_task_id,
        placeholder_created_at=placeholder_at,
        input_message_id=preparation.input_message_id,
        turn_id=preparation.turn_id,
        execution_mode="serial",
        context_anchor=preparation.context_anchor(internal_task_id, org_id),
    )
    external_task_id = await handler.start(
        message_id=preparation.output_message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        content=body.content,
        params=business_params,
        metadata=metadata,
    )
    user_message = _build_user_message(
        body, conversation_id, preparation.input_message_id,
        preparation.turn_id, created_at,
    )
    if user_message:
        record_user_activity(
            db, user_id=user_id, event_type="message_sent", org_id=org_id,
            source="web", resource_type="message", resource_id=user_message.id,
            metadata={"conversation_id": conversation_id},
        )
    return GenerateResponse(
        task_id=client_task_id or external_task_id,
        user_message=user_message,
        assistant_message=_build_assistant_message(
            body, conversation_id, preparation.output_message_id,
            preparation.input_message_id, preparation.turn_id, placeholder_at,
            client_task_id,
        ),
        operation=body.operation,
        generation_type=GenerationType.CHAT.value,
    )


def _build_task_payload(
    *, handler: Any, body: GenerateRequest, business_params: dict[str, Any],
    internal_task_id: str, client_task_id: str, assistant_message_id: str,
    conversation_id: str, user_id: str, placeholder_at: datetime,
) -> dict[str, Any]:
    model_id = body.model or DEFAULT_MODEL_ID
    request_params = {
        "content": handler._extract_text_content(body.content),
        "model_id": model_id,
        **handler._serialize_params(dict(business_params)),
    }
    payload = handler._build_task_data(
        task_id=client_task_id, message_id=assistant_message_id,
        conversation_id=conversation_id, user_id=user_id, task_type="chat",
        status="pending", model_id=model_id, request_params=request_params,
        metadata=TaskMetadata(
            client_task_id=client_task_id,
            placeholder_created_at=placeholder_at,
        ),
    )
    payload.update({
        "id": internal_task_id,
        "execution_mode": "serial",
        "delivery_context": {"actor": True, "channel": "web"},
    })
    return payload


def _business_params(body: GenerateRequest) -> dict[str, Any]:
    params = {
        key: value for key, value in (body.params or {}).items()
        if key not in {"client_task_id", "placeholder_created_at"}
    }
    if body.model:
        params["model"] = body.model
    params["operation"] = body.operation.value
    return params


def _input_payload(
    body: GenerateRequest,
    message_id: str | None,
    created_at: datetime,
) -> dict[str, Any]:
    if message_id is None:
        return {}
    return {
        "id": message_id,
        "content": [part.model_dump(mode="json") for part in body.content],
        "client_request_id": body.client_request_id,
        "created_at": created_at.isoformat(),
    }


def _output_payload(
    body: GenerateRequest,
    message_id: str,
    created_at: datetime,
) -> dict[str, Any]:
    generation_params = {"type": GenerationType.CHAT.value}
    if body.model:
        generation_params["model"] = body.model
    return {
        "id": message_id,
        "content": [],
        "status": MessageStatus.STREAMING.value,
        "generation_params": generation_params,
        "created_at": created_at.isoformat(),
    }


def _build_user_message(
    body: GenerateRequest,
    conversation_id: str,
    message_id: str,
    turn_id: str,
    created_at: datetime,
) -> Message | None:
    if body.operation in {MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE}:
        return None
    return Message(
        id=message_id, conversation_id=conversation_id, role=MessageRole.USER,
        content=body.content, status=MessageStatus.COMPLETED, created_at=created_at,
        client_request_id=body.client_request_id, turn_id=turn_id,
    )


def _build_assistant_message(
    body: GenerateRequest,
    conversation_id: str,
    message_id: str,
    input_message_id: str,
    turn_id: str,
    created_at: datetime,
    client_task_id: str,
) -> Message:
    generation_params: dict[str, Any] = {"type": GenerationType.CHAT.value}
    if body.model:
        generation_params["model"] = body.model
    return Message(
        id=message_id, conversation_id=conversation_id, role=MessageRole.ASSISTANT,
        content=[], status=MessageStatus.STREAMING, created_at=created_at,
        task_id=client_task_id, generation_params=GenerationParams(**generation_params),
        turn_id=turn_id, reply_to_message_id=input_message_id,
    )


def _stable_id(namespace: uuid.UUID, request_id: str) -> str:
    return str(uuid.uuid5(namespace, request_id))


def _required_identity(value: str | None) -> str:
    if not value:
        raise RuntimeError("GENERATION_REQUEST_IDENTITY_MISSING")
    return value
