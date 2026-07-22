"""电商图两阶段的统一生成事务准备。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from schemas.message import (
    GenerateRequest, GenerateResponse, GenerationType, MessageOperation,
)
from services.generation_lifecycle import GenerationLifecycle
from services.handlers.base import TaskMetadata
from services.user_activity_service import record_user_activity

from api.routes.message_image_preparation import (
    _assistant_message,
    _business_params,
    _input_payload,
    _output_payload,
    _required,
    _stable_id,
    _user_message,
    prepare_and_start_image_generation,
)


_ECOM_TURN_NAMESPACE = uuid.UUID("9fc9a8d6-b642-4c19-b088-735bd518b10e")
_ECOM_INPUT_NAMESPACE = uuid.UUID("d5235121-4768-4a90-8d3c-91d6fabf3a7e")
_ECOM_TASK_NAMESPACE = uuid.UUID("76fbbb30-6c83-4f15-adc0-b3cc049a83c7")


@dataclass
class PreparedEcomPlanMetadata(TaskMetadata):
    """电商策划 Handler 消费的已准备任务元数据。"""

    prepared_task_id: str | None = None


async def prepare_and_start_ecom_generation(
    *, db: Any, handler: Any, conversation_service: Any,
    conversation_id: str, user_id: str, org_id: str | None,
    request_id: str, body: GenerateRequest,
) -> GenerateResponse:
    """按 image_task_meta 区分策划与生图，两阶段均先原子准备。"""
    if body.params is None:
        body.params = {}
    image_task_meta = body.params.get("image_task_meta")
    if image_task_meta and isinstance(image_task_meta, list):
        handler.prepare_phase2_params(body.content, body.params)
        return await prepare_and_start_image_generation(
            db=db, handler=handler, conversation_service=conversation_service,
            conversation_id=conversation_id, user_id=user_id, org_id=org_id,
            request_id=request_id, body=body,
            response_generation_type=GenerationType.IMAGE_ECOM,
        )
    return await _prepare_and_start_plan(
        db=db, handler=handler, conversation_service=conversation_service,
        conversation_id=conversation_id, user_id=user_id, org_id=org_id,
        request_id=request_id, body=body,
    )


async def _prepare_and_start_plan(
    *, db: Any, handler: Any, conversation_service: Any,
    conversation_id: str, user_id: str, org_id: str | None,
    request_id: str, body: GenerateRequest,
) -> GenerateResponse:
    conversation = await conversation_service.get_conversation(
        conversation_id, user_id, org_id,
    )
    body.params["_prefetched_summary"] = conversation.get("context_summary")
    body.params["_org_id"] = org_id
    created_at = body.created_at or datetime.now(timezone.utc)
    placeholder_at = body.placeholder_created_at or datetime.now(timezone.utc)
    existing_output = body.operation in {
        MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE,
    }
    turn_id = None if existing_output else _stable_id(_ECOM_TURN_NAMESPACE, request_id)
    input_id = None if existing_output else _stable_id(_ECOM_INPUT_NAMESPACE, request_id)
    task_id = _stable_id(_ECOM_TASK_NAMESPACE, request_id)
    client_task_id = _required(body.client_task_id)
    preparation = GenerationLifecycle(db).prepare(
        request_id=request_id, operation=body.operation.value,
        conversation_id=conversation_id, user_id=user_id, org_id=org_id,
        turn_id=turn_id, input_message=_input_payload(body, input_id, created_at),
        output_message=_output_payload(body, placeholder_at, GenerationType.IMAGE_ECOM),
        tasks=[{
            "id": task_id, "external_task_id": client_task_id,
            "client_task_id": client_task_id, "type": "image", "status": "running",
            "model_id": "qwen-vl-max", "request_params": {
                "phase": "plan", **handler._serialize_params(_business_params(body)),
            },
            "placeholder_created_at": placeholder_at.isoformat(),
            "execution_mode": "serial", "delivery_context": {"channel": "web"},
        }],
    )
    metadata = PreparedEcomPlanMetadata(
        client_task_id=client_task_id, placeholder_created_at=placeholder_at,
        input_message_id=preparation.input_message_id, turn_id=preparation.turn_id,
        context_anchor=preparation.context_anchor(task_id, org_id),
        prepared_task_id=task_id,
    )
    external_task_id = await handler.start(
        message_id=preparation.output_message_id, conversation_id=conversation_id,
        user_id=user_id, content=body.content, params=_business_params(body),
        metadata=metadata,
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
        metadata={"conversation_id": conversation_id, "generation_type": "image_ecom",
                  "operation": body.operation.value},
    )
    return GenerateResponse(
        task_id=client_task_id, user_message=user_message,
        assistant_message=_assistant_message(
            body, conversation_id, preparation.output_message_id,
            preparation.input_message_id, preparation.turn_id, placeholder_at,
            GenerationType.IMAGE_ECOM,
        ),
        operation=body.operation, generation_type=GenerationType.IMAGE_ECOM.value,
    )
