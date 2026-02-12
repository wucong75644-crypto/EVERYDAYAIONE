"""
统一消息路由

提供统一的消息生成入口 /messages/generate。
支持聊天、图片、视频等多种生成类型。
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger

from api.deps import CurrentUser, CurrentUserId, Database, TaskLimitSvc
from core.limiter import limiter, RATE_LIMITS
from schemas.message import (
    ContentPart,
    DeleteMessageResponse,
    GenerateRequest,
    GenerateResponse,
    GenerationType,
    GenerationParams,
    Message,
    MessageListResult,
    MessageOperation,
    MessageResponse,
    MessageRole,
    MessageStatus,
    TextPart,
    infer_generation_type,
)
from services.message_service import MessageService
from services.conversation_service import ConversationService
from services.handlers import get_handler

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["消息"])
message_router = APIRouter(prefix="/messages", tags=["消息"])


def get_message_service(db: Database) -> MessageService:
    """获取消息服务实例"""
    return MessageService(db)


def get_conversation_service(db: Database) -> ConversationService:
    """获取对话服务实例"""
    return ConversationService(db)


# ============================================================
# 统一消息生成 API
# ============================================================


@router.post("/generate", response_model=GenerateResponse, summary="统一消息生成")
@limiter.limit(RATE_LIMITS["message_stream"])
async def generate_message(
    request: Request,
    conversation_id: str,
    body: GenerateRequest,
    current_user: CurrentUser,
    db: Database,
    task_limit_service: TaskLimitSvc,
):
    """
    统一消息生成入口

    根据 generation_type 或 content 自动路由到对应 Handler：
    - chat: 流式聊天（WebSocket 推送）
    - image: 图片生成（异步任务）
    - video: 视频生成（异步任务）

    支持三种操作：
    - send: 发送新消息（创建用户消息 + 创建 AI 消息）
    - retry: 重试失败的 AI 消息（不创建用户消息 + 原地更新）
    - regenerate: 重新生成成功的 AI 消息（创建用户消息 + 创建 AI 消息）
    """
    user_id = current_user["id"]

    # 1. 检查任务限制
    if task_limit_service:
        await task_limit_service.check_and_acquire(user_id, conversation_id)

    # 2. 推断生成类型
    gen_type = body.generation_type or infer_generation_type(body.content)

    # 3. 验证对话权限
    conversation_service = get_conversation_service(db)
    await conversation_service.get_conversation(conversation_id, user_id)

    # 4. 创建用户消息（send/regenerate）
    user_message: Optional[Message] = None
    if body.operation != MessageOperation.RETRY:
        user_message = await _create_user_message(
            db=db,
            conversation_id=conversation_id,
            content=body.content,
            created_at=body.created_at,
            client_request_id=body.client_request_id,
        )

    # 5. 处理助手消息
    if body.operation == MessageOperation.RETRY:
        # retry: 更新原失败消息状态

        if not body.original_message_id:
            raise HTTPException(status_code=400, detail="retry 操作必须提供 original_message_id")

        # 校验原消息状态
        original_msg = db.table("messages").select("id, status, conversation_id").eq(
            "id", body.original_message_id
        ).single().execute()

        if not original_msg.data:
            raise HTTPException(status_code=404, detail="原消息不存在")

        if original_msg.data["conversation_id"] != conversation_id:
            raise HTTPException(status_code=403, detail="消息不属于该对话")

        if original_msg.data["status"] != MessageStatus.FAILED.value:
            raise HTTPException(status_code=400, detail="retry 只能用于失败消息")

        # 检查是否有进行中的任务
        existing_task = db.table("tasks").select("id").eq(
            "placeholder_message_id", body.original_message_id
        ).in_("status", ["pending", "running"]).execute()

        if existing_task.data:
            raise HTTPException(status_code=409, detail="该消息正在处理中，请稍候")

        assistant_message_id = body.original_message_id
        assistant_message = await _reset_message_for_retry(
            db=db,
            message_id=assistant_message_id,
            gen_type=gen_type,
            model=body.model,
            params=body.params,
        )
    elif body.operation == MessageOperation.REGENERATE:
        # regenerate: 校验原消息并生成新 ID（不创建占位符）

        if body.original_message_id:
            # 校验原消息状态（必须是成功消息）
            original_msg = db.table("messages").select("id, status, conversation_id").eq(
                "id", body.original_message_id
            ).single().execute()

            if original_msg.data and original_msg.data["status"] == MessageStatus.FAILED.value:
                raise HTTPException(status_code=400, detail="regenerate 只能用于成功消息，失败消息请用 retry")

        # 只生成 ID，不创建占位符消息（前端负责创建占位符）
        assistant_message_id = body.assistant_message_id or str(uuid.uuid4())

        # 构建 generation_params（只设置 type，前端用来判断占位符类型）
        generation_params_obj = GenerationParams(type=gen_type)

        # 构造返回用的虚拟 Message（不存储到数据库）
        assistant_message = Message(
            id=assistant_message_id,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=[],
            status=MessageStatus.PENDING,
            created_at=body.placeholder_created_at or datetime.utcnow(),
            generation_params=generation_params_obj,
        )
    else:
        # send: 只生成 ID，不创建占位符消息（前端负责创建占位符）
        assistant_message_id = body.assistant_message_id or str(uuid.uuid4())

        # 构建 generation_params（只设置 type，前端用来判断占位符类型）
        generation_params_obj = GenerationParams(type=gen_type)

        # 构造返回用的虚拟 Message（不存储到数据库）
        assistant_message = Message(
            id=assistant_message_id,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=[],
            status=MessageStatus.PENDING,
            created_at=body.placeholder_created_at or datetime.utcnow(),
            generation_params=generation_params_obj,
        )

    # 6. 获取 Handler 并启动任务
    handler = get_handler(gen_type, db)

    # 🔥 分离元数据和业务参数
    from services.handlers.base import TaskMetadata

    # 🔍 日志：接收到的 client_task_id
    logger.info(
        f"[message.py] Received request | "
        f"operation={body.operation} | gen_type={gen_type} | "
        f"client_task_id={body.client_task_id} | "
        f"assistant_message_id={assistant_message_id}"
    )

    # 构建元数据
    metadata = TaskMetadata(
        client_task_id=body.client_task_id,
        placeholder_created_at=body.placeholder_created_at,
    )

    # 构建纯业务参数（排除元数据字段）
    business_params = {}
    if body.params:
        for k, v in body.params.items():
            if k not in {"client_task_id", "placeholder_created_at"}:
                business_params[k] = v

    # 添加 model（如果有）
    if body.model:
        business_params["model"] = body.model

    external_task_id = await handler.start(
        message_id=assistant_message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        content=body.content,
        params=business_params,
        metadata=metadata,
    )

    # 7. 确定返回的 client_task_id（Handler 已保存到数据库）
    client_task_id = body.client_task_id or external_task_id

    # 🔍 日志：返回给前端的 task_id
    logger.info(
        f"[message.py] Returning to frontend | "
        f"client_task_id={client_task_id} | "
        f"external_task_id={external_task_id} | "
        f"assistant_message_id={assistant_message_id}"
    )

    # 8. 更新消息的 task_id（仅 retry 操作，因为消息已存在）
    if body.operation == MessageOperation.RETRY:
        db.table("messages").update({
            "task_id": client_task_id,
        }).eq("id", assistant_message_id).execute()

    # 🔥 返回 client_task_id（前端已订阅）
    return GenerateResponse(
        task_id=client_task_id,
        user_message=user_message,
        assistant_message=assistant_message,
        operation=body.operation,
    )


def _build_generation_params(
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


async def _create_user_message(
    db: Database,
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


async def _reset_message_for_retry(
    db: Database,
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
    generation_params = _build_generation_params(gen_type, model, params)

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


# ============================================================
# ❌ 旧 API 已删除
# ============================================================
# 旧的 /create API 已删除，请使用 /generate 端点


@router.get("", response_model=MessageListResult, summary="获取消息列表")
async def get_messages(
    conversation_id: str,
    current_user_id: CurrentUserId,
    limit: int = Query(default=100, ge=1, le=1000, description="每页数量"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    before_id: Optional[str] = Query(default=None, description="获取此消息之前的消息"),
    service: MessageService = Depends(get_message_service),
):
    """
    获取对话的消息列表

    按创建时间降序返回（从新到旧），支持分页加载历史消息。
    """
    result = await service.get_messages(
        conversation_id=conversation_id,
        user_id=current_user_id,
        limit=limit,
        offset=offset,
        before_id=before_id,
    )
    return result


@router.get("/{message_id}", response_model=MessageResponse, summary="获取消息详情")
async def get_message(
    conversation_id: str,
    message_id: str,
    current_user: CurrentUser,
    service: MessageService = Depends(get_message_service),
):
    """获取单条消息的详细信息"""
    result = await service.get_message(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=current_user["id"],
    )
    return result


# ==================== 独立消息路由 ====================


@message_router.delete("/{message_id}", response_model=DeleteMessageResponse, summary="删除消息")
async def delete_message(
    message_id: str,
    current_user: CurrentUser,
    service: MessageService = Depends(get_message_service),
):
    """
    删除单条消息

    权限验证：只能删除自己对话中的消息
    """
    result = await service.delete_message(
        message_id=message_id,
        user_id=current_user["id"],
    )
    return result
