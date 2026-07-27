"""Web Chat 到 Conversation Actor 持久队列的入口。"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

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
    """唤醒已由 prepare_generation 原子创建的 Web Chat task。"""
    if metadata.context_anchor is None:
        raise RuntimeError("ACTOR_PREPARED_CONTEXT_ANCHOR_MISSING")
    if not metadata.input_message_id or not metadata.turn_id:
        raise RuntimeError("ACTOR_ENQUEUE_TURN_ANCHOR_MISSING")
    await _publish_wakeup(conversation_id, handler.org_id)
    logger.info(
        "actor_web_prepared_wakeup | "
        f"task_id={metadata.context_anchor.task_id} | "
        f"external_task_id={external_task_id} | "
        f"conversation_id={conversation_id} | turn_id={metadata.turn_id}"
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
