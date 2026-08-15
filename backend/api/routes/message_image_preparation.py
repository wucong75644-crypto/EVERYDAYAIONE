"""Web 图片批次的统一生成事务准备与供应商启动。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NoReturn

from loguru import logger

from schemas.message import (
    GenerateRequest, GenerateResponse, GenerationParams, GenerationType,
    Message, MessageOperation, MessageRole, MessageStatus,
    serialize_content_parts,
)
from services.generation_lifecycle import GenerationLifecycle
from services.handlers.base import TaskMetadata
from services.handlers.image_request_settings import (
    build_canonical_image_request,
    ImageCountValidationError,
    resolve_image_generation_settings,
)
from services.user_activity_service import record_user_activity

from api.routes.message_media_failure import (
    RuntimeMediaNotOwned,
    RuntimeMediaOwnershipReconcileRequired,
    RuntimeMediaPartialOwnership,
    fail_closed_prepared_media,
    read_prepared_media_ownership,
)


_IMAGE_TURN_NAMESPACE = uuid.UUID("fd68022e-6cda-444a-843c-53832f58de7f")
_IMAGE_INPUT_NAMESPACE = uuid.UUID("0c68e63b-0748-4971-b907-54e291279441")
_IMAGE_TASK_NAMESPACE = uuid.UUID("07932385-a335-40ba-8e76-ea254b674a6a")
_IMAGE_BATCH_NAMESPACE = uuid.UUID("49852944-8734-42d6-8a43-6f2694375334")
_RUNTIME_IMAGE_BATCH_LIMIT = 10
_ECOM_IMAGE_BATCH_LIMIT = 4


@dataclass
class PreparedImageTaskMetadata(TaskMetadata):
    """图片 Handler 消费的已准备批次元数据。"""

    prepared_task_ids: tuple[str, ...] = ()
    prepared_batch_id: str | None = None


async def prepare_and_start_image_generation(
    *,
    db: Any,
    handler: Any,
    conversation_service: Any,
    conversation_id: str,
    user_id: str,
    org_id: str | None,
    request_id: str,
    body: GenerateRequest,
    response_generation_type: GenerationType = GenerationType.IMAGE,
) -> GenerateResponse:
    """原子准备普通图片批次，再交给 ImageHandler 提交供应商。"""
    conversation = await conversation_service.get_conversation(
        conversation_id, user_id, org_id,
    )
    if body.params is None:
        body.params = {}
    body.params["_org_id"] = org_id
    settings = _resolve_and_preflight_image_settings(
        handler=handler, body=body, user_id=user_id,
        response_generation_type=response_generation_type,
    )
    existing_output = body.operation in {MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE}
    turn_id = None if existing_output else _stable_id(_IMAGE_TURN_NAMESPACE, request_id)
    input_id = None if existing_output else _stable_id(_IMAGE_INPUT_NAMESPACE, request_id)
    batch_id = _stable_id(_IMAGE_BATCH_NAMESPACE, request_id)
    task_ids = tuple(
        _stable_id(_IMAGE_TASK_NAMESPACE, f"{request_id}:{index}")
        for index in range(settings["num_images"])
    )
    created_at = body.created_at or datetime.now(timezone.utc)
    placeholder_at = body.placeholder_created_at or datetime.now(timezone.utc)
    task_payloads = _task_payloads(
        handler=handler, body=body, settings=settings, task_ids=task_ids,
        batch_id=batch_id, conversation_id=conversation_id, user_id=user_id,
        org_id=org_id, placeholder_at=placeholder_at,
    )
    lifecycle = GenerationLifecycle(db)
    preparation = lifecycle.prepare(
        request_id=request_id, operation=body.operation.value,
        conversation_id=conversation_id, user_id=user_id, org_id=org_id,
        turn_id=turn_id,
        input_message=_input_payload(body, input_id, created_at),
        output_message=_output_payload(body, placeholder_at, response_generation_type),
        tasks=task_payloads,
    )
    metadata = PreparedImageTaskMetadata(
        client_task_id=_required(body.client_task_id),
        placeholder_created_at=placeholder_at,
        input_message_id=preparation.input_message_id,
        turn_id=preparation.turn_id,
        context_anchor=preparation.context_anchor(task_ids[0], org_id),
        prepared_task_ids=task_ids,
        prepared_batch_id=batch_id,
    )
    try:
        if response_generation_type == GenerationType.IMAGE_ECOM:
            external_task_id = await handler.start(
                message_id=preparation.output_message_id,
                conversation_id=conversation_id, user_id=user_id,
                content=body.content, params=_business_params(body),
                metadata=metadata,
            )
        else:
            external_task_id = await _submit_runtime_image_tasks(
                db=db, conversation_id=conversation_id, user_id=user_id,
                org_id=org_id, request_id=request_id, task_ids=task_ids,
                task_payloads=task_payloads, preparation=preparation,
                model_id=settings["model_id"], batch_id=batch_id,
            )
    except RuntimeMediaNotOwned as error:
        await fail_closed_prepared_media(
            db=db, lifecycle=lifecycle, task_ids=task_ids, task_payloads=task_payloads,
            message_id=preparation.output_message_id,
            operation=body.operation, params=body.params, media_kind="image",
            outcome=error.outcome, org_id=org_id, user_id=user_id,
        )
    except RuntimeMediaPartialOwnership:
        raise
    except Exception as error:
        if response_generation_type == GenerationType.IMAGE:
            await _close_or_isolate_runtime_submit_error(
                error=error, db=db, lifecycle=lifecycle, task_ids=task_ids,
                task_payloads=task_payloads,
                message_id=preparation.output_message_id,
                operation=body.operation, params=body.params,
                org_id=org_id, user_id=user_id,
            )
        from core.exceptions import AppException
        if isinstance(error, AppException) and error.code == "IMAGE_GENERATION_FAILED":
            from api.routes.message_generation_helpers import finalize_image_request_failure
            finalize_image_request_failure(
                db=db, message_id=preparation.output_message_id,
                operation=body.operation, params=body.params,
                error_code=error.code, error_message=error.message,
            )
        raise
    user_message = _user_message(
        body, conversation_id, preparation.input_message_id,
        preparation.turn_id, created_at,
    )
    return _complete_image_request(
        db=db, body=body, conversation_id=conversation_id, user_id=user_id,
        org_id=org_id, preparation=preparation, user_message=user_message,
        external_task_id=external_task_id, placeholder_at=placeholder_at,
        generation_type=response_generation_type,
    )


def _resolve_and_preflight_image_settings(
    *, handler: Any, body: GenerateRequest, user_id: str,
    response_generation_type: GenerationType,
) -> dict[str, Any]:
    business_params = _business_params(body)
    is_runtime_image = response_generation_type == GenerationType.IMAGE
    try:
        settings = resolve_image_generation_settings(
            params=business_params,
            has_image_urls=bool(handler._extract_image_urls(body.content)),
            max_images=(
                _RUNTIME_IMAGE_BATCH_LIMIT
                if is_runtime_image else _ECOM_IMAGE_BATCH_LIMIT
            ),
            batch_prompt_limit=(
                _RUNTIME_IMAGE_BATCH_LIMIT if is_runtime_image else 8
            ),
            strict_count=is_runtime_image,
        )
    except ImageCountValidationError as error:
        from core.exceptions import AppException

        raise AppException(
            code="IMAGE_BATCH_COUNT_INVALID",
            message="普通对话每次可生成 1 到 10 张图片",
            status_code=400,
        ) from error
    if is_runtime_image:
        handler._check_balance(user_id, settings["total_credits"])
        if body.operation != MessageOperation.REGENERATE_SINGLE:
            body.params["num_images"] = settings["num_images"]
    else:
        handler.preflight(user_id, body.content, business_params)
        if settings["num_images"] > _ECOM_IMAGE_BATCH_LIMIT:
            raise RuntimeError("IMAGE_PREPARED_TASK_COUNT_INVALID")
    return settings


def _complete_image_request(
    *, db: Any, body: GenerateRequest, conversation_id: str, user_id: str,
    org_id: str | None, preparation: Any, user_message: Message | None,
    external_task_id: str, placeholder_at: datetime,
    generation_type: GenerationType,
) -> GenerateResponse:
    if user_message:
        record_user_activity(
            db, user_id=user_id, event_type="message_sent", org_id=org_id,
            source="web", resource_type="message", resource_id=user_message.id,
            metadata={"conversation_id": conversation_id},
        )
    record_user_activity(
        db, user_id=user_id, event_type="task_created", org_id=org_id,
        source="web", resource_type="task", resource_id=external_task_id,
        metadata={"conversation_id": conversation_id,
                  "generation_type": generation_type.value,
                  "operation": body.operation.value},
    )
    return GenerateResponse(
        task_id=_required(body.client_task_id), user_message=user_message,
        assistant_message=_assistant_message(
            body, conversation_id, preparation.output_message_id,
            preparation.input_message_id, preparation.turn_id, placeholder_at,
            generation_type,
        ),
        operation=body.operation, generation_type=generation_type.value,
    )


async def _close_or_isolate_runtime_submit_error(
    *, error: Exception, db: Any, lifecycle: GenerationLifecycle,
    task_ids: tuple[str, ...], task_payloads: list[dict[str, Any]],
    message_id: str, operation: MessageOperation,
    params: dict[str, Any] | None, org_id: str | None, user_id: str,
) -> NoReturn:
    logger.error(
        "runtime_media_image_submit_failed | error_type={} | sqlstate={}",
        type(error).__name__, _sqlstate(error),
    )
    ownership = await read_prepared_media_ownership(db, task_ids)
    if ownership == "none":
        await fail_closed_prepared_media(
            db=db, lifecycle=lifecycle, task_ids=task_ids,
            task_payloads=task_payloads, message_id=message_id,
            operation=operation, params=params, media_kind="image",
            outcome="submit_failed_no_ownership", org_id=org_id,
            user_id=user_id,
        )
    raise RuntimeMediaOwnershipReconcileRequired(ownership) from error


async def _submit_runtime_image_tasks(
    *, db: Any, conversation_id: str, user_id: str, org_id: str | None,
    request_id: str, task_ids: tuple[str, ...],
    task_payloads: list[dict[str, Any]], preparation: Any, model_id: str,
    batch_id: str,
) -> str:
    from api.routes.message_media_runtime import submit_runtime_image_batch_ingress

    receipt = await submit_runtime_image_batch_ingress(
        db=db, conversation_id=conversation_id, user_id=user_id, org_id=org_id,
        input_message_id=preparation.input_message_id,
        output_message_id=preparation.output_message_id,
        turn_id=preparation.turn_id, batch_id=batch_id, model_id=model_id,
        items=[{
            "task_id": task_id,
            "idempotency_key": f"{request_id}:{task_id}",
            "request": task["request_params"],
        } for task_id, task in zip(task_ids, task_payloads)],
    )
    if receipt.outcome == "partial_ownership":
        raise RuntimeMediaPartialOwnership()
    if not receipt.runtime_owned:
        raise RuntimeMediaNotOwned(receipt.outcome)
    if not receipt.accepted:
        raise RuntimeError("RUNTIME_MEDIA_INGRESS_NOT_OWNED")
    return receipt.run_id or task_ids[0]


def _task_payloads(
    *, handler: Any, body: GenerateRequest, settings: dict[str, Any],
    task_ids: tuple[str, ...], batch_id: str, conversation_id: str,
    user_id: str, org_id: str | None, placeholder_at: datetime,
) -> list[dict[str, Any]]:
    params = _business_params(body)
    default_prompt = handler._extract_text_content(body.content)
    prompts = params.get("_batch_prompts") or []
    single_index = int(params.get("image_index", 0))
    payloads = []
    for offset, task_id in enumerate(task_ids):
        prompt = prompts[offset].get("prompt", default_prompt) if offset < len(prompts) else default_prompt
        image_index = single_index if body.operation == MessageOperation.REGENERATE_SINGLE else offset
        request_params = build_canonical_image_request(
            prompt=prompt, model_id=settings["model_id"],
            serialized_params=handler._serialize_params(params),
        )
        payloads.append({
            "id": task_id, "client_task_id": body.client_task_id,
            "user_id": user_id, "org_id": org_id,
            "conversation_id": conversation_id, "type": "image",
            "status": "preparing", "model_id": settings["model_id"],
            "request_params": request_params,
            "placeholder_created_at": placeholder_at.isoformat(),
            "execution_mode": "serial", "delivery_context": {"channel": "web"},
            "image_index": image_index, "batch_id": batch_id,
        })
    return payloads


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
        "content": serialize_content_parts(body.content),
        "client_request_id": body.client_request_id,
        "created_at": created_at.isoformat(),
    }


def _output_payload(
    body: GenerateRequest, created_at: datetime, generation_type: GenerationType,
) -> dict[str, Any]:
    params = _generation_params(body, generation_type)
    render = (body.params or {}).get("_render", {})
    default_text = "电商图生成中" if generation_type == GenerationType.IMAGE_ECOM else "图片生成中"
    text = render.get("placeholder_text") or default_text
    return {
        "id": _required(body.assistant_message_id),
        "content": [{"type": "text", "text": text}],
        "status": MessageStatus.PENDING.value,
        "generation_params": params,
        "created_at": created_at.isoformat(),
    }


def _generation_params(
    body: GenerateRequest, generation_type: GenerationType = GenerationType.IMAGE,
) -> dict[str, Any]:
    result: dict[str, Any] = {"type": generation_type.value}
    if body.model:
        result["model"] = body.model
    for key in ("num_images", "aspect_ratio", "resolution", "output_format", "_render"):
        if key in (body.params or {}):
            result[key] = body.params[key]
    return result


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
    generation_type: GenerationType,
) -> Message:
    return Message(
        id=message_id, conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=[], status=MessageStatus.PENDING, created_at=created_at,
        task_id=body.client_task_id,
        generation_params=GenerationParams(**_generation_params(body, generation_type)),
        turn_id=turn_id, reply_to_message_id=input_id,
    )


def _stable_id(namespace: uuid.UUID, value: str) -> str:
    return str(uuid.uuid5(namespace, value))


def _required(value: str | None) -> str:
    if not value:
        raise RuntimeError("GENERATION_REQUEST_IDENTITY_MISSING")
    return value


def _sqlstate(error: Exception) -> str:
    value = getattr(error, "sqlstate", None)
    if isinstance(value, str) and value:
        return value
    diagnostic = getattr(error, "diag", None)
    value = getattr(diagnostic, "sqlstate", None)
    return value if isinstance(value, str) and value else "unknown"
