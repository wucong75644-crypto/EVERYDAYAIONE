"""
消息路由

提供消息的发送、查询接口。
"""

from collections.abc import AsyncGenerator
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from api.deps import CurrentUser, CurrentUserId, Database, TaskLimitSvc
from core.limiter import limiter, RATE_LIMITS
from loguru import logger
from schemas.message import (
    DeleteMessageResponse,
    MessageCreate,
    MessageListResult,
    MessageResponse,
    SendMessageRequest,
)
from services.message_service import MessageService
from services.message_stream_service import MessageStreamService
from services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["消息"])
message_router = APIRouter(prefix="/messages", tags=["消息"])


def get_message_service(db: Database) -> MessageService:
    """获取消息服务实例"""
    return MessageService(db)


def get_message_stream_service(db: Database) -> MessageStreamService:
    """获取流式消息服务实例"""
    message_service = MessageService(db)
    conversation_service = ConversationService(db)
    return MessageStreamService(db, message_service, conversation_service)


@router.post("/create", response_model=MessageResponse, summary="创建消息")
@limiter.limit("60/minute")
async def create_message(
    request: Request,
    conversation_id: str,
    body: MessageCreate,
    current_user: CurrentUser,
    service: MessageService = Depends(get_message_service),
):
    """
    直接创建消息（用于图像生成等场景）

    - **content**: 消息内容
    - **role**: 消息角色（user/assistant）
    - **image_url**: 图片 URL（可选）
    - **video_url**: 视频 URL（可选）
    - **credits_cost**: 消耗积分
    - **is_error**: 是否为错误消息
    - **generation_params**: 生成参数（用于重新生成时继承）
    """
    result = await service.create_message(
        conversation_id=conversation_id,
        user_id=current_user["id"],
        content=body.content,
        role=body.role.value,
        image_url=body.image_url,
        video_url=body.video_url,
        credits_cost=body.credits_cost,
        is_error=body.is_error,
        created_at=body.created_at,
        generation_params=body.generation_params,
        client_request_id=body.client_request_id,
    )
    return result


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

    按创建时间正序返回，支持分页加载历史消息。
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
    """
    获取单条消息的详细信息
    """
    result = await service.get_message(
        message_id=message_id,
        user_id=current_user["id"],
    )
    return result


@router.post("/stream", summary="流式发送消息")
@limiter.limit(RATE_LIMITS["message_stream"])
async def send_message_stream(
    request: Request,
    conversation_id: str,
    body: SendMessageRequest,
    current_user: CurrentUser,
    task_limit_service: TaskLimitSvc,
    service: MessageStreamService = Depends(get_message_stream_service),
):
    """
    流式发送消息到对话（SSE）

    返回 Server-Sent Events 流，包含以下事件类型：
    - user_message: 用户消息已创建
    - start: AI 开始生成
    - content: AI 响应内容块（逐字返回）
    - done: 生成完成，包含完整的 assistant_message
    - error: 发生错误

    示例响应流：
    ```
    data: {"type": "user_message", "data": {...}}
    data: {"type": "start", "data": {"model": "gemini-3-flash"}}
    data: {"type": "content", "data": {"text": "你好"}}
    data: {"type": "content", "data": {"text": "！"}}
    data: {"type": "done", "data": {"assistant_message": {...}, "credits_consumed": 5}}
    data: [DONE]
    ```
    """
    # 任务限制检查（降级处理：服务不可用时跳过）
    if task_limit_service:
        await task_limit_service.check_and_acquire(current_user["id"], conversation_id)

    async def stream_with_cleanup() -> AsyncGenerator[str, None]:
        """流式响应包装器，确保任务槽位释放"""
        try:
            async for chunk in service.send_message_stream(
                conversation_id=conversation_id,
                user_id=current_user["id"],
                content=body.content,
                model_id=body.model_id,
                image_url=body.image_url,
                video_url=body.video_url,
                thinking_effort=body.thinking_effort,
                thinking_mode=body.thinking_mode,
                client_request_id=body.client_request_id,
                created_at=body.created_at,
            ):
                yield chunk
        finally:
            if task_limit_service:
                await task_limit_service.release(current_user["id"], conversation_id)

    return StreamingResponse(
        stream_with_cleanup(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{message_id}/regenerate", summary="重新生成失败的消息")
@limiter.limit(RATE_LIMITS["message_regenerate"])
async def regenerate_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    current_user: CurrentUser,
    service: MessageStreamService = Depends(get_message_stream_service),
):
    """
    重新生成失败的消息（流式）

    用于重新尝试生成之前失败的 AI 回复。返回 SSE 流式事件。

    事件类型：
    - start: AI 开始生成
    - content: AI 响应内容块（逐字返回）
    - done: 生成完成，包含更新后的消息
    - error: 再次失败

    示例响应流：
    ```
    data: {"type": "start", "data": {"model": "gemini-3-flash"}}
    data: {"type": "content", "data": {"text": "你好"}}
    data: {"type": "done", "data": {"assistant_message": {...}, "credits_consumed": 5}}
    data: [DONE]
    ```
    """
    return StreamingResponse(
        service.regenerate_message_stream(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=current_user["id"],
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 独立消息路由 ====================


@message_router.delete("/{message_id}", response_model=DeleteMessageResponse, summary="删除消息")
async def delete_message(
    message_id: str,
    current_user: CurrentUser,
    service: MessageService = Depends(get_message_service),
):
    """
    删除单条消息

    - **message_id**: 消息 ID

    权限验证：只能删除自己对话中的消息
    """
    result = await service.delete_message(
        message_id=message_id,
        user_id=current_user["id"],
    )
    return result
