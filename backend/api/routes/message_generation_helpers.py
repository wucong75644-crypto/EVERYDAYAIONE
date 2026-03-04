"""
消息生成辅助函数

从 message.py 提取的生成业务逻辑，包括：
- 用户消息创建
- retry / regenerate / send 操作处理
- 生成任务启动
- 消息重置
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from loguru import logger

from schemas.message import (
    ContentPart,
    GenerationParams,
    GenerationType,
    Message,
    MessageOperation,
    MessageRole,
    MessageStatus,
    TextPart,
)


async def handle_retry_operation(
    db,
    conversation_id: str,
    original_message_id: Optional[str],
    gen_type: GenerationType,
    model: Optional[str],
    params: Optional[Dict[str, Any]],
) -> tuple[str, Message]:
    """
    处理 retry 操作

    Args:
        db: 数据库连接
        conversation_id: 对话 ID
        original_message_id: 原消息 ID
        gen_type: 生成类型
        model: 模型 ID
        params: 参数

    Returns:
        (assistant_message_id, assistant_message)

    Raises:
        HTTPException: 验证失败或消息状态不正确
    """
    if not original_message_id:
        raise HTTPException(status_code=400, detail="retry 操作必须提供 original_message_id")

    # 校验原消息状态
    original_msg = db.table("messages").select("id, status, conversation_id").eq(
        "id", original_message_id
    ).single().execute()

    if not original_msg.data:
        raise HTTPException(status_code=404, detail="原消息不存在")

    if original_msg.data["conversation_id"] != conversation_id:
        raise HTTPException(status_code=403, detail="消息不属于该对话")

    if original_msg.data["status"] != MessageStatus.FAILED.value:
        raise HTTPException(status_code=400, detail="retry 只能用于失败消息")

    # 检查是否有进行中的任务
    existing_task = db.table("tasks").select("id").eq(
        "placeholder_message_id", original_message_id
    ).in_("status", ["pending", "running"]).execute()

    if existing_task.data:
        raise HTTPException(status_code=409, detail="该消息正在处理中，请稍候")

    # 重置消息状态
    assistant_message_id = original_message_id
    assistant_message = await reset_message_for_retry(
        db=db,
        message_id=assistant_message_id,
        gen_type=gen_type,
        model=model,
        params=params,
    )

    return assistant_message_id, assistant_message


async def handle_regenerate_single_operation(
    db,
    conversation_id: str,
    original_message_id: Optional[str],
    params: Optional[Dict[str, Any]],
) -> tuple[str, Message]:
    """
    处理 regenerate_single 操作（单图重新生成）

    复用原消息 ID，仅将 content[image_index] 置为 null 占位。
    不创建用户消息，不创建新 AI 消息。

    Returns:
        (assistant_message_id, assistant_message)

    Raises:
        HTTPException: 验证失败
    """
    if not original_message_id:
        raise HTTPException(status_code=400, detail="regenerate_single 必须提供 original_message_id")

    image_index = params.get("image_index") if params else None
    if image_index is None:
        raise HTTPException(status_code=400, detail="regenerate_single 必须提供 image_index")

    # 校验原消息
    original_msg = db.table("messages").select(
        "id, status, conversation_id, content, generation_params"
    ).eq("id", original_message_id).single().execute()

    if not original_msg.data:
        raise HTTPException(status_code=404, detail="原消息不存在")

    if original_msg.data["conversation_id"] != conversation_id:
        raise HTTPException(status_code=403, detail="消息不属于该对话")

    msg_status = original_msg.data["status"]
    if msg_status not in (MessageStatus.COMPLETED.value, MessageStatus.FAILED.value):
        raise HTTPException(status_code=400, detail="只能对已完成或已失败的消息重新生成单图")

    # 校验 image_index 合法性
    content = original_msg.data.get("content", [])
    if isinstance(content, str):
        content = json.loads(content)
    gen_params = original_msg.data.get("generation_params", {})
    if isinstance(gen_params, str):
        gen_params = json.loads(gen_params)
    num_images = gen_params.get("num_images", len(content))
    if image_index < 0 or image_index >= num_images:
        raise HTTPException(status_code=400, detail=f"image_index 超出范围 [0, {num_images})")

    # 检查是否有进行中的任务（针对同一张图）
    existing_task = db.table("tasks").select("id").eq(
        "placeholder_message_id", original_message_id
    ).eq("image_index", image_index).in_(
        "status", ["pending", "running"]
    ).execute()
    if existing_task.data:
        raise HTTPException(status_code=409, detail="该图片正在重新生成中，请稍候")

    # 更新 content[image_index] 为进行中占位
    if isinstance(content, list) and image_index < len(content):
        content[image_index] = {"type": "image", "url": None}
        db.table("messages").update({"content": content}).eq("id", original_message_id).execute()

    # 构造返回对象（只提取 type 字段，gen_params 可能含 num_images 等额外字段）
    gen_type_str = gen_params.get("type") if gen_params else None
    generation_params_obj = GenerationParams(type=GenerationType(gen_type_str)) if gen_type_str else None

    assistant_message = Message(
        id=original_message_id,
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=content,
        status=MessageStatus(msg_status),
        created_at=datetime.now(timezone.utc),
        generation_params=generation_params_obj,
    )

    return original_message_id, assistant_message


async def handle_regenerate_or_send_operation(
    db,
    conversation_id: str,
    operation: MessageOperation,
    original_message_id: Optional[str],
    assistant_message_id: Optional[str],
    placeholder_created_at: Optional[datetime],
    gen_type: GenerationType,
    params: Optional[Dict[str, Any]] = None,
) -> tuple[str, Message]:
    """
    处理 regenerate 或 send 操作

    Args:
        db: 数据库连接
        conversation_id: 对话 ID
        operation: 操作类型
        original_message_id: 原消息 ID
        assistant_message_id: 助手消息 ID
        placeholder_created_at: 占位符创建时间
        gen_type: 生成类型

    Returns:
        (assistant_message_id, assistant_message)

    Raises:
        HTTPException: regenerate 验证失败
    """
    if operation == MessageOperation.REGENERATE and original_message_id:
        # 校验原消息状态（必须是成功消息）
        original_msg = db.table("messages").select("id, status, conversation_id").eq(
            "id", original_message_id
        ).single().execute()

        if original_msg.data and original_msg.data["status"] == MessageStatus.FAILED.value:
            raise HTTPException(
                status_code=400,
                detail="regenerate 只能用于成功消息，失败消息请用 retry"
            )

    # 生成助手消息 ID
    assistant_message_id = assistant_message_id or str(uuid.uuid4())

    # 构建 generation_params（只设置 type，前端用来判断占位符类型）
    generation_params_obj = GenerationParams(type=gen_type)

    # Media 类型（image/video）：将占位符 insert 到 messages 表
    # 这样刷新页面后占位符能通过 GET /messages 自然加载，无需 taskRestoration 手动重建
    # Chat 类型保持虚拟（不入库），因为 Chat 的流式 chunk 依赖 optimisticMessages
    if gen_type in (GenerationType.IMAGE, GenerationType.VIDEO):
        _PLACEHOLDER_TEXT = {
            GenerationType.IMAGE: "图片生成中",
            GenerationType.VIDEO: "视频生成中",
        }
        placeholder_text = _PLACEHOLDER_TEXT[gen_type]

        # 构建 generation_params（包含前端渲染占位符所需的参数）
        gen_params: Dict[str, Any] = {"type": gen_type.value}
        if params:
            # 按类型提取前端渲染所需的参数
            _PARAM_KEYS = {
                GenerationType.IMAGE: ("num_images", "aspect_ratio", "resolution", "output_format"),
                GenerationType.VIDEO: ("aspect_ratio", "n_frames", "remove_watermark"),
            }
            for key in _PARAM_KEYS.get(gen_type, ()):
                if key in params:
                    gen_params[key] = params[key]

        placeholder_data = {
            "id": assistant_message_id,
            "conversation_id": conversation_id,
            "role": MessageRole.ASSISTANT.value,
            "content": [{"type": "text", "text": placeholder_text}],
            "status": MessageStatus.PENDING.value,
            "generation_params": gen_params,
            "credits_cost": 0,
        }
        if placeholder_created_at:
            placeholder_data["created_at"] = placeholder_created_at.isoformat()

        try:
            db.table("messages").insert(placeholder_data).execute()
            logger.info(
                f"Media placeholder saved to DB | "
                f"message_id={assistant_message_id} | type={gen_type.value}"
            )
        except Exception as e:
            # 占位符入库失败不应阻断任务，降级为虚拟占位符（与 Chat 行为一致）
            logger.warning(
                f"Failed to save media placeholder to DB, continuing | "
                f"message_id={assistant_message_id} | error={e}"
            )

    # 构造返回用的 Message 对象
    assistant_message = Message(
        id=assistant_message_id,
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=[],
        status=MessageStatus.PENDING,
        created_at=placeholder_created_at or datetime.now(timezone.utc),
        generation_params=generation_params_obj,
    )

    return assistant_message_id, assistant_message


async def start_generation_task(
    db,
    handler,
    assistant_message_id: str,
    conversation_id: str,
    user_id: str,
    content: List[ContentPart],
    model: Optional[str],
    params: Optional[Dict[str, Any]],
    client_task_id: Optional[str],
    placeholder_created_at: Optional[datetime],
    operation: MessageOperation,
) -> str:
    """
    启动生成任务

    Args:
        db: 数据库连接
        handler: Handler 实例
        assistant_message_id: 助手消息 ID
        conversation_id: 对话 ID
        user_id: 用户 ID
        content: 内容
        model: 模型 ID
        params: 参数
        client_task_id: 客户端任务 ID
        placeholder_created_at: 占位符创建时间
        operation: 操作类型

    Returns:
        external_task_id: 外部任务 ID
    """
    from services.handlers.base import TaskMetadata

    # 日志：接收到的 client_task_id
    logger.info(
        f"[message.py] Starting task | "
        f"operation={operation} | "
        f"client_task_id={client_task_id} | "
        f"assistant_message_id={assistant_message_id}"
    )

    # 构建元数据
    metadata = TaskMetadata(
        client_task_id=client_task_id,
        placeholder_created_at=placeholder_created_at,
    )

    # 构建纯业务参数（排除元数据字段）
    business_params = {}
    if params:
        for k, v in params.items():
            if k not in {"client_task_id", "placeholder_created_at"}:
                business_params[k] = v

    # 添加 model（如果有）
    if model:
        business_params["model"] = model

    # 添加 operation（handler 用于区分 regenerate_single 等操作）
    business_params["operation"] = operation.value

    # 启动任务
    external_task_id = await handler.start(
        message_id=assistant_message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        content=content,
        params=business_params,
        metadata=metadata,
    )

    # 确定返回的 client_task_id
    final_client_task_id = client_task_id or external_task_id

    # 日志：返回给前端的 task_id
    logger.info(
        f"[message.py] Task started | "
        f"client_task_id={final_client_task_id} | "
        f"external_task_id={external_task_id} | "
        f"assistant_message_id={assistant_message_id}"
    )

    # 更新消息的 task_id（retry 和 regenerate_single 操作，因为消息已存在）
    if operation in (MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE):
        db.table("messages").update({
            "task_id": final_client_task_id,
        }).eq("id", assistant_message_id).execute()

    return external_task_id


def build_generation_params(
    gen_type: GenerationType,
    model: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建生成参数（公共函数）

    用于 retry、regenerate、send 操作复用，减少重复代码。

    Args:
        gen_type: 生成类型（chat/image/video）
        model: 模型 ID
        params: 其他参数

    Returns:
        generation_params 字典
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
    """
    重置失败消息用于重试

    将原失败消息的状态重置为 pending，清空内容和错误信息
    """
    # 使用公共函数构建 generation_params
    generation_params = build_generation_params(gen_type, model, params)

    # 更新消息：重置状态、清空内容和错误
    update_data = {
        "status": MessageStatus.PENDING.value,
        "content": [],
        "error": None,
        "generation_params": generation_params,
        "task_id": None,  # 清空旧的 task_id
    }

    result = db.table("messages").update(update_data).eq("id", message_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="原消息不存在")

    msg_data = result.data[0]

    # 转换 generation_params 为 Pydantic 模型（只提取 type）
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
