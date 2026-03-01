"""
统一消息路由

提供统一的消息生成入口 /messages/generate。
支持聊天、图片、视频等多种生成类型。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import CurrentUser, CurrentUserId, Database, TaskLimitSvc
from core.limiter import limiter, RATE_LIMITS
from schemas.message import (
    DeleteMessageResponse,
    GenerateRequest,
    GenerateResponse,
    Message,
    MessageListResult,
    MessageOperation,
    MessageResponse,
    infer_generation_type,
)
from services.message_service import MessageService
from services.conversation_service import ConversationService
from services.handlers import get_handler

from api.routes.message_generation_helpers import (
    handle_retry_operation,
    handle_regenerate_or_send_operation,
    start_generation_task,
    create_user_message,
)

# 向后兼容别名：test_placeholder_to_db.py 使用了带下划线前缀的名称
_handle_retry_operation = handle_retry_operation
_handle_regenerate_or_send_operation = handle_regenerate_or_send_operation
_start_generation_task = start_generation_task
_create_user_message = create_user_message

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
        user_message = await create_user_message(
            db=db,
            conversation_id=conversation_id,
            content=body.content,
            created_at=body.created_at,
            client_request_id=body.client_request_id,
        )

    # 5. 处理助手消息（根据操作类型）
    if body.operation == MessageOperation.RETRY:
        assistant_message_id, assistant_message = await handle_retry_operation(
            db=db,
            conversation_id=conversation_id,
            original_message_id=body.original_message_id,
            gen_type=gen_type,
            model=body.model,
            params=body.params,
        )
    else:
        assistant_message_id, assistant_message = await handle_regenerate_or_send_operation(
            db=db,
            conversation_id=conversation_id,
            operation=body.operation,
            original_message_id=body.original_message_id,
            assistant_message_id=body.assistant_message_id,
            placeholder_created_at=body.placeholder_created_at,
            gen_type=gen_type,
        )

    # 6. 获取 Handler 并启动任务
    handler = get_handler(gen_type, db)

    external_task_id = await start_generation_task(
        db=db,
        handler=handler,
        assistant_message_id=assistant_message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        content=body.content,
        model=body.model,
        params=body.params,
        client_task_id=body.client_task_id,
        placeholder_created_at=body.placeholder_created_at,
        operation=body.operation,
    )

    # 7. 确定返回的 client_task_id（Handler 已保存到数据库）
    client_task_id = body.client_task_id or external_task_id

    # 8. 返回结果
    return GenerateResponse(
        task_id=client_task_id,
        user_message=user_message,
        assistant_message=assistant_message,
        operation=body.operation,
    )


# ============================================================
# 消息 CRUD API
# ============================================================


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
