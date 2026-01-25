"""
对话路由

提供对话的创建、查询、更新、删除接口。
"""

from fastapi import APIRouter, Depends, Query

from api.deps import CurrentUser, CurrentUserId, Database
from schemas.conversation import (
    ConversationCreate,
    ConversationListResult,
    ConversationResponse,
    ConversationUpdate,
)
from services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["对话"])


def get_conversation_service(db: Database) -> ConversationService:
    """获取对话服务实例"""
    return ConversationService(db)


@router.post("", response_model=ConversationResponse, summary="创建对话")
async def create_conversation(
    request: ConversationCreate,
    current_user: CurrentUser,
    service: ConversationService = Depends(get_conversation_service),
):
    """
    创建新对话

    - **title**: 对话标题（可选，默认为"新对话"）
    - **model_id**: 模型 ID（可选）
    """
    result = await service.create_conversation(
        user_id=current_user["id"],
        title=request.title,
        model_id=request.model_id,
    )
    return result


@router.get("", response_model=ConversationListResult, summary="获取对话列表")
async def get_conversation_list(
    current_user_id: CurrentUserId,
    limit: int = Query(default=50, ge=1, le=100, description="每页数量"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    service: ConversationService = Depends(get_conversation_service),
):
    """
    获取当前用户的对话列表

    按更新时间倒序排列，包含最后一条消息摘要。
    """
    result = await service.get_conversation_list(
        user_id=current_user_id,
        limit=limit,
        offset=offset,
    )
    return result


@router.get("/{conversation_id}", response_model=ConversationResponse, summary="获取对话详情")
async def get_conversation(
    conversation_id: str,
    current_user: CurrentUser,
    service: ConversationService = Depends(get_conversation_service),
):
    """
    获取单个对话的详细信息
    """
    result = await service.get_conversation(
        conversation_id=conversation_id,
        user_id=current_user["id"],
    )
    return result


@router.put("/{conversation_id}", response_model=ConversationResponse, summary="更新对话")
async def update_conversation(
    conversation_id: str,
    request: ConversationUpdate,
    current_user: CurrentUser,
    service: ConversationService = Depends(get_conversation_service),
):
    """
    更新对话信息

    - **title**: 新标题（可选）
    - **model_id**: 模型 ID（可选）
    """
    result = await service.update_conversation(
        conversation_id=conversation_id,
        user_id=current_user["id"],
        title=request.title,
        model_id=request.model_id,
    )
    return result


@router.delete("/{conversation_id}", summary="删除对话")
async def delete_conversation(
    conversation_id: str,
    current_user: CurrentUser,
    service: ConversationService = Depends(get_conversation_service),
):
    """
    删除对话

    会同时删除该对话下的所有消息。
    """
    await service.delete_conversation(
        conversation_id=conversation_id,
        user_id=current_user["id"],
    )
    return {"message": "对话已删除"}
