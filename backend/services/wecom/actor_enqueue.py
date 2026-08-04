"""企业微信消息到 Conversation Actor 的原子幂等入口。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from psycopg.types.json import Jsonb

from schemas.message import (
    FilePart,
    ImagePart,
    TextPart,
    serialize_content_parts,
)
from schemas.wecom import WecomIncomingMessage
from services.handlers.base import TaskMetadata


_WECOM_ACTOR_NAMESPACE = uuid.UUID("954af423-38df-4e02-80e8-08f599fe323c")


@dataclass(frozen=True)
class WecomActorEnqueueResult:
    task_id: str
    input_message_id: str
    output_message_id: str
    already_enqueued: bool


async def enqueue_wecom_message(
    *,
    handler: Any,
    msg: WecomIncomingMessage,
    user_id: str,
    conversation_id: str,
    image_urls: list[str],
    file_payload: dict[str, Any] | None = None,
    stream_context: Mapping[str, Any] | None = None,
) -> WecomActorEnqueueResult:
    """使用稳定 ID 原子创建消息和 Actor task，并 best-effort 唤醒 Worker。"""
    if not msg.msgid:
        raise RuntimeError("WECOM_ACTOR_MSGID_MISSING")
    ids = _stable_ids(msg, user_id)
    model_id, chat_settings = _load_chat_settings(handler.db, conversation_id)
    input_content = _build_content(
        msg.text_content or "", image_urls, file_payload,
    )
    metadata = TaskMetadata(
        client_task_id=f"wecom:{msg.msgid}",
        input_message_id=ids["input"],
        turn_id=ids["turn"],
        execution_mode="serial",
    )
    request_params = {
        "content": msg.text_content or "",
        "model": model_id,
        "thinking_mode": chat_settings.get("thinking_mode"),
        "_org_id": msg.org_id,
    }
    task_data = handler._build_task_data(
        task_id=f"wecom:{msg.msgid}",
        message_id=ids["output"],
        conversation_id=conversation_id,
        user_id=user_id,
        task_type="chat",
        status="pending",
        model_id=model_id,
        request_params=request_params,
        metadata=metadata,
    )
    task_data["id"] = ids["task"]
    delivery_context = _delivery_context(msg, ids["task"], stream_context)
    from core.config import get_settings

    settings = get_settings()
    rpc_name = (
        "enqueue_wecom_runtime_turn_v5"
        if settings.agent_runtime_ingress_enabled
        else "enqueue_wecom_generation_turn_v2"
    )
    params = {
            "p_task_data": Jsonb(task_data),
            "p_input_message_id": ids["input"],
            "p_output_message_id": ids["output"],
            "p_turn_id": ids["turn"],
            "p_input_content": Jsonb(input_content),
            "p_delivery_context": Jsonb(delivery_context),
    }
    if rpc_name == "enqueue_wecom_runtime_turn_v3":
        params.update({
            "p_agent_definition_id": settings.agent_runtime_agent_definition_id,
            "p_agent_definition_revision":
                settings.agent_runtime_agent_definition_revision,
            "p_idempotency_key": f"wecom:{msg.msgid}",
        })
    if rpc_name == "enqueue_wecom_runtime_turn_v5":
        fact_response = handler.db.rpc("get_agent_runtime_definition_fact", {
            "p_agent_key": settings.agent_runtime_agent_definition_id,
            "p_definition_revision": settings.agent_runtime_agent_definition_revision,
        }).execute()
        fact = fact_response.data if fact_response else None
        if not isinstance(fact, dict) or fact.get("outcome") == "not_found":
            raise RuntimeError("WECOM_RUNTIME_DEFINITION_FACT_UNAVAILABLE")
        definition_hash = str(fact.get("definition_hash") or "")
        catalog_revision = str(fact.get("catalog_revision") or "")
        if not definition_hash or not catalog_revision:
            raise RuntimeError("WECOM_RUNTIME_DEFINITION_FACT_INVALID")
        params.update({
            "p_agent_definition_hash": definition_hash,
            "p_effective_toolset_revision": catalog_revision,
            "p_effective_toolset_hash": None,
            "p_release_revision": settings.agent_runtime_release_revision,
            "p_idempotency_key": f"wecom:{msg.msgid}",
        })
    response = handler.db.rpc(rpc_name, params).execute()
    result = response.data if response else None
    if not isinstance(result, dict) or not result.get("task_id"):
        raise RuntimeError("WECOM_ACTOR_ENQUEUE_RESULT_INVALID")

    if not bool(result.get("runtime_owned")):
        from services.conversation_worker import RedisConversationWakeup
        await RedisConversationWakeup().publish(conversation_id, msg.org_id)
    return WecomActorEnqueueResult(
        task_id=str(result["task_id"]),
        input_message_id=str(result.get("input_message_id") or ids["input"]),
        output_message_id=str(result.get("output_message_id") or ids["output"]),
        already_enqueued=bool(result.get("already_enqueued")),
    )


def _stable_ids(msg: WecomIncomingMessage, user_id: str) -> dict[str, str]:
    root = f"{msg.org_id or 'personal'}:{msg.corp_id}:{user_id}:{msg.msgid}"
    return {
        name: str(uuid.uuid5(_WECOM_ACTOR_NAMESPACE, f"{root}:{name}"))
        for name in ("task", "input", "output", "turn")
    }


def stable_wecom_task_id(msg: WecomIncomingMessage, user_id: str) -> str:
    """返回与企微原子入队一致的稳定 task ID。"""
    return _stable_ids(msg, user_id)["task"]


def _load_chat_settings(db: Any, conversation_id: str) -> tuple[str, dict[str, Any]]:
    response = db.rpc("get_wecom_generation_context", {
        "p_user_id": None,
        "p_conversation_id": conversation_id,
    }).execute()
    row = response.data if response else None
    if not row:
        raise RuntimeError("WECOM_ACTOR_CONVERSATION_MISSING")
    settings = row.get("chat_settings")
    if not isinstance(settings, dict):
        settings = {}
    from services.agent.runtime.model_resolution import resolve_runtime_model

    return resolve_runtime_model(row.get("model_id")).model_id, settings


def _build_content(
    text: str,
    image_urls: list[str],
    file_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    parts: list[Any] = []
    if text:
        parts.append(TextPart(text=text))
    parts.extend(ImagePart(url=url) for url in image_urls)
    if file_payload:
        parts.append(FilePart(**file_payload))
    if not parts:
        parts.append(TextPart(text="（用户发送了一张图片）"))
    return serialize_content_parts(parts)


def _delivery_context(
    msg: WecomIncomingMessage,
    task_id: str,
    stream_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    context = {
        "actor": True,
        "channel": "wecom",
        "transport": msg.channel,
        "org_id": msg.org_id,
        "corp_id": msg.corp_id,
        "chatid": msg.chatid,
        "chattype": msg.chattype,
        "wecom_userid": msg.wecom_userid,
    }
    if msg.channel == "smart_robot" and stream_context:
        context.update({
            "stream_task_id": task_id,
            "stream_req_id": stream_context.get("req_id"),
            "stream_id": stream_context.get("stream_id"),
            "stream_started_at": stream_context.get("started_at"),
        })
    return context
