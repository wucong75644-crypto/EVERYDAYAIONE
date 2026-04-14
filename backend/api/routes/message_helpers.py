"""
消息创建辅助函数

从 message_generation_helpers.py 提取的消息创建和重置逻辑。
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from schemas.message import (
    ContentPart,
    GenerationParams,
    GenerationType,
    Message,
    MessageRole,
    MessageStatus,
    TextPart,
)


def build_generation_params(
    gen_type: GenerationType,
    model: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建生成参数（公共函数）

    用于 retry、regenerate、send 操作复用。
    """
    generation_params = {"type": gen_type.value}
    if model:
        generation_params["model"] = model
    if params:
        generation_params.update(params)
    return generation_params


async def create_user_message(
    db,
    conversation_id: str,
    content: List[ContentPart],
    created_at: Optional[datetime] = None,
    client_request_id: Optional[str] = None,
) -> Message:
    """创建用户消息"""
    message_id = str(uuid.uuid4())

    # 转换 ContentPart 为字典
    content_dicts = []
    for part in content:
        if isinstance(part, TextPart):
            content_dicts.append({"type": "text", "text": part.text})
        elif hasattr(part, "model_dump"):
            content_dicts.append(part.model_dump())
        elif isinstance(part, dict):
            content_dicts.append(part)

    message_data = {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": MessageRole.USER.value,
        "content": content_dicts,
        "status": MessageStatus.COMPLETED.value,
        "credits_cost": 0,
    }

    if created_at:
        message_data["created_at"] = created_at.isoformat()
    if client_request_id:
        message_data["client_request_id"] = client_request_id

    result = db.table("messages").insert(message_data).execute()

    if not result.data:
        raise Exception("创建用户消息失败")

    msg_data = result.data[0]

    return Message(
        id=msg_data["id"],
        conversation_id=msg_data["conversation_id"],
        role=MessageRole(msg_data["role"]),
        content=content,
        status=MessageStatus(msg_data["status"]),
        created_at=datetime.fromisoformat(msg_data["created_at"].replace("Z", "+00:00")),
        client_request_id=msg_data.get("client_request_id"),
    )


async def reset_message_for_retry(
    db,
    message_id: str,
    gen_type: GenerationType,
    model: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Message:
    """重置失败消息用于重试"""
    generation_params = build_generation_params(gen_type, model, params)

    update_data = {
        "status": MessageStatus.PENDING.value,
        "content": [],
        "is_error": False,
        "generation_params": generation_params,
        "task_id": None,
    }

    result = db.table("messages").update(update_data).eq("id", message_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="原消息不存在")

    msg_data = result.data[0]

    generation_params_obj = None
    if msg_data.get("generation_params"):
        gen_type_str = msg_data["generation_params"].get("type")
        if gen_type_str:
            generation_params_obj = GenerationParams(type=GenerationType(gen_type_str))

    return Message(
        id=msg_data["id"],
        conversation_id=msg_data["conversation_id"],
        role=MessageRole(msg_data["role"]),
        content=[],
        status=MessageStatus(msg_data["status"]),
        created_at=datetime.fromisoformat(msg_data["created_at"].replace("Z", "+00:00")),
        generation_params=generation_params_obj,
    )
