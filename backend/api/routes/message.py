"""
消息路由

提供消息的发送、查询接口。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from api.deps import CurrentUser, CurrentUserId, Database
from schemas.message import (
    DeleteMessageResponse,
    MessageCreate,
    MessageListResult,
    MessageResponse,
    SendMessageRequest,
    SendMessageResponse,
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
async def create_message(
    conversation_id: str,
    request: MessageCreate,
    current_user: CurrentUser,
    service: MessageService = Depends(get_message_service),
):
    """
    直接创建消息（用于图像生成等场景）

    - **content**: 消息内容
    - **role**: 消息角色（user/assistant）
    - **image_url**: 图片 URL（可选）
    - **credits_cost**: 消耗积分
    """
    result = await service.create_message(
        conversation_id=conversation_id,
        user_id=current_user["id"],
        content=request.content,
        role=request.role.value,
        image_url=request.image_url,
        credits_cost=request.credits_cost,
    )
    return result


@router.post("", response_model=SendMessageResponse, summary="发送消息")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    current_user: CurrentUser,
    service: MessageService = Depends(get_message_service),
):
    """
    发送消息到对话

    - **content**: 消息内容
    - **model_id**: 模型 ID（可选，用于指定 AI 响应模型）
    - **image_url**: 图片 URL（可选，用于 VQA）
    - **video_url**: 视频 URL（可选，用于视频 QA）
    - **image_size**: 图片尺寸（可选，用于图片生成）
    - **image_count**: 图片数量（可选，1-4张）
    """
    result = await service.send_message(
        conversation_id=conversation_id,
        user_id=current_user["id"],
        content=request.content,
        model_id=request.model_id,
        image_url=request.image_url,
        video_url=request.video_url,
        thinking_effort=request.thinking_effort,
        thinking_mode=request.thinking_mode,
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
async def send_message_stream(
    conversation_id: str,
    request: SendMessageRequest,
    current_user: CurrentUser,
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
    return StreamingResponse(
        service.send_message_stream(
            conversation_id=conversation_id,
            user_id=current_user["id"],
            content=request.content,
            model_id=request.model_id,
            image_url=request.image_url,
            video_url=request.video_url,
            thinking_effort=request.thinking_effort,
            thinking_mode=request.thinking_mode,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{message_id}/regenerate", summary="重新生成失败的消息")
async def regenerate_message(
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
