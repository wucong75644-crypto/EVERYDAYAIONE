"""消息生成请求的 Turn 锚点解析与写入。"""

from typing import Any

from fastapi import HTTPException
from loguru import logger

from schemas.message import MessageRole


def resolve_existing_turn_anchor(
    db: Any,
    conversation_id: str,
    assistant_message_id: str,
) -> tuple[str, str]:
    """读取既有回复的持久化 Turn，不为历史脏数据生成新的随机关系。"""
    result = db.table("messages").select(
        "id, conversation_id, turn_id, reply_to_message_id, created_at"
    ).eq("id", assistant_message_id).maybe_single().execute()
    message = result.data if result else None
    if not message or message.get("conversation_id") != conversation_id:
        raise HTTPException(status_code=409, detail="无法确定原消息的上下文锚点")

    if message.get("turn_id") and message.get("reply_to_message_id"):
        return message["reply_to_message_id"], message["turn_id"]

    if message.get("turn_id") or message.get("reply_to_message_id"):
        raise HTTPException(status_code=409, detail="原消息的上下文锚点不完整，无法重试")

    query = (
        db.table("messages")
        .select("id, turn_id")
        .eq("conversation_id", conversation_id)
        .eq("role", MessageRole.USER.value)
    )
    if message.get("created_at"):
        query = query.lt("created_at", message["created_at"])
    previous = query.order("created_at", desc=True).limit(1).execute()
    if not previous.data:
        raise HTTPException(status_code=409, detail="原消息缺少可恢复的用户输入锚点")

    previous_user = previous.data[0]
    input_message_id = previous_user["id"]
    turn_id = previous_user.get("turn_id")
    if not turn_id:
        raise HTTPException(status_code=409, detail="原消息缺少持久化的用户 Turn，无法重试")

    logger.warning(
        "legacy_context_anchor_recovered | "
        f"conversation_id={conversation_id} | input_message_id={input_message_id} | "
        f"assistant_message_id={assistant_message_id} | turn_id={turn_id}"
    )
    return input_message_id, turn_id
