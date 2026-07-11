"""消息生成请求在占位消息变更前的准备流程。"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import (
    ContentPart, GenerationType, Message, MessageOperation, infer_generation_type,
)
from services.user_activity_service import record_user_activity


async def resolve_generation_context(request: Any, body: Any) -> GenerationType:
    """解析生成类型，并在可用时把用户位置注入任务参数。"""
    from config.smart_model_config import SMART_MODEL_ID, resolve_auto_model
    from services.ip_location_service import extract_client_ip, get_location_by_ip

    client_ip = extract_client_ip(request)
    location_task = asyncio.create_task(get_location_by_ip(client_ip))
    if body.model == SMART_MODEL_ID:
        if body.generation_type in (
            GenerationType.IMAGE, GenerationType.IMAGE_ECOM, GenerationType.VIDEO,
        ):
            gen_type = body.generation_type
            body.model = resolve_auto_model(gen_type, body.content, None)
        else:
            gen_type = GenerationType.CHAT
            if body.params is None:
                body.params = {}
            body.params["_is_smart_mode"] = True
            body.model = resolve_auto_model(gen_type, body.content, None)
    elif body.generation_type:
        gen_type = body.generation_type
    else:
        gen_type = infer_generation_type(body.content)

    try:
        user_location = await location_task
    except Exception as exc:
        logger.warning(f"IP location lookup failed | ip={client_ip} | {exc}")
        user_location = None
    if user_location:
        if body.params is None:
            body.params = {}
        body.params["_user_location"] = user_location
    return gen_type


def preflight_image_request(
    handler: Any,
    user_id: str,
    content: List[ContentPart],
    params: Optional[Dict[str, Any]],
    model: Optional[str],
    operation: MessageOperation,
) -> None:
    """构造与正式任务一致的业务参数并执行图片积分预检。"""
    preflight_params = dict(params or {})
    if model:
        preflight_params["model"] = model
    preflight_params["operation"] = operation.value
    handler.preflight(user_id, content, preflight_params)


async def prepare_generation_request(
    db: Any,
    conversation_id: str,
    body: Any,
    gen_type: GenerationType,
    user_id: str,
    org_id: Optional[str],
    handler: Any,
    conversation_service: Any,
    create_user_message_fn: Any,
) -> tuple[Any, Dict[str, Any], Optional[Message]]:
    """完成权限校验、图片预检和可选用户消息创建。"""
    user_message: Optional[Message] = None
    no_user_message_operations = {MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE}
    needs_user_message = body.operation not in no_user_message_operations

    if gen_type == GenerationType.IMAGE:
        conversation = await conversation_service.get_conversation(
            conversation_id, user_id, org_id,
        )
        preflight_image_request(handler, user_id, body.content, body.params, body.model, body.operation)
        if needs_user_message:
            user_message = await create_user_message_fn(
                db=db, conversation_id=conversation_id, content=body.content,
                created_at=body.created_at, client_request_id=body.client_request_id,
            )
    elif needs_user_message:
        conversation, user_message = await asyncio.gather(
            conversation_service.get_conversation(conversation_id, user_id, org_id),
            create_user_message_fn(
                db=db, conversation_id=conversation_id, content=body.content,
                created_at=body.created_at, client_request_id=body.client_request_id,
            ),
        )
    else:
        conversation = await conversation_service.get_conversation(
            conversation_id, user_id, org_id,
        )

    if user_message:
        record_user_activity(
            db, user_id=user_id, event_type="message_sent", org_id=org_id,
            source="web", resource_type="message", resource_id=user_message.id,
            metadata={"conversation_id": conversation_id},
        )
    return handler, conversation, user_message
