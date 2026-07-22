"""Web Chat 到 Conversation Actor 持久队列的入口。"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger
from psycopg.types.json import Jsonb

from services.handlers.base import TaskMetadata


_ACTOR_TASK_NAMESPACE = uuid.UUID("dad50f04-bfa6-4e49-853a-543f8f856998")


async def enqueue_web_chat(
    *,
    handler: Any,
    external_task_id: str,
    message_id: str,
    conversation_id: str,
    user_id: str,
    model_id: str,
    content: list[Any],
    params: dict[str, Any],
    metadata: TaskMetadata,
) -> str:
    """原子 enqueue Web Chat，并以 Redis 进行 best-effort 唤醒。"""
    if not metadata.input_message_id or not metadata.turn_id:
        raise RuntimeError("ACTOR_ENQUEUE_TURN_ANCHOR_MISSING")
    request_params = {
        "content": handler._extract_text_content(content),
        "model_id": model_id,
        **handler._serialize_params(params),
    }
    task_data = handler._build_task_data(
        task_id=external_task_id,
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        task_type="chat",
        status="pending",
        model_id=model_id,
        request_params=request_params,
        metadata=metadata,
    )
    task_data["id"] = (
        metadata.context_anchor.task_id
        if metadata.context_anchor
        else stable_actor_task_id(
            user_id=user_id,
            conversation_id=conversation_id,
            external_task_id=external_task_id,
        )
    )
    delivery_context = {"actor": True, "channel": "web"}
    response = handler.db.rpc(
        "enqueue_generation_turn",
        {
            "p_task_data": Jsonb(task_data),
            "p_input_message_id": metadata.input_message_id,
            "p_turn_id": metadata.turn_id,
            "p_execution_mode": metadata.execution_mode,
            "p_delivery_context": Jsonb(delivery_context),
        },
    ).execute()
    result = response.data if response else None
    if not isinstance(result, dict) or not result.get("task_id"):
        raise RuntimeError("ACTOR_ENQUEUE_RESULT_INVALID")
    await _publish_wakeup(conversation_id, handler.org_id)
    logger.info(
        "actor_web_enqueued | "
        f"task_id={result['task_id']} | external_task_id={external_task_id} | "
        f"conversation_id={conversation_id} | turn_id={metadata.turn_id} | "
        f"already_enqueued={result.get('already_enqueued', False)}"
    )
    return external_task_id


def stable_actor_task_id(
    *,
    user_id: str,
    conversation_id: str,
    external_task_id: str,
) -> str:
    key = f"{user_id}:{conversation_id}:{external_task_id}"
    return str(uuid.uuid5(_ACTOR_TASK_NAMESPACE, key))


async def _publish_wakeup(
    conversation_id: str,
    org_id: str | None,
) -> None:
    from services.conversation_worker import RedisConversationWakeup

    await RedisConversationWakeup().publish(conversation_id, org_id)
