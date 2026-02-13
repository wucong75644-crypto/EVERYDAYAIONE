"""
对话路由

提供对话的创建、查询、更新、删除接口。
"""

from fastapi import APIRouter, Depends, Query
from loguru import logger

from api.deps import CurrentUser, CurrentUserId, Database
from core.exceptions import (
    AppException,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
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
    try:
        result = await service.create_conversation(
            user_id=current_user["id"],
            title=request.title,
            model_id=request.model_id,
        )
        return result
    except (
        ValidationError,
        AuthenticationError,
        ConflictError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Error in create_conversation route | user_id={current_user['id']} | "
            f"title={request.title} | model_id={request.model_id} | error={str(e)}"
        )
        raise AppException(
            code="ROUTE_CREATE_CONVERSATION_ERROR",
            message="创建对话失败",
            status_code=500,
        )


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
    try:
        result = await service.get_conversation_list(
            user_id=current_user_id,
            limit=limit,
            offset=offset,
        )
        return result
    except (
        ValidationError,
        AuthenticationError,
        ConflictError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Error in get_conversation_list route | user_id={current_user_id} | "
            f"limit={limit} | offset={offset} | error={str(e)}"
        )
        raise AppException(
            code="ROUTE_GET_CONVERSATION_LIST_ERROR",
            message="获取对话列表失败",
            status_code=500,
        )


@router.get("/{conversation_id}", response_model=ConversationResponse, summary="获取对话详情")
async def get_conversation(
    conversation_id: str,
    current_user: CurrentUser,
    service: ConversationService = Depends(get_conversation_service),
):
    """
    获取单个对话的详细信息
    """
    try:
        result = await service.get_conversation(
            conversation_id=conversation_id,
            user_id=current_user["id"],
        )
        return result
    except (
        ValidationError,
        AuthenticationError,
        ConflictError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Error in get_conversation route | conversation_id={conversation_id} | "
            f"user_id={current_user['id']} | error={str(e)}"
        )
        raise AppException(
            code="ROUTE_GET_CONVERSATION_ERROR",
            message="获取对话失败",
            status_code=500,
        )


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
    try:
        result = await service.update_conversation(
            conversation_id=conversation_id,
            user_id=current_user["id"],
            title=request.title,
            model_id=request.model_id,
        )
        return result
    except (
        ValidationError,
        AuthenticationError,
        ConflictError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Error in update_conversation route | conversation_id={conversation_id} | "
            f"user_id={current_user['id']} | title={request.title} | "
            f"model_id={request.model_id} | error={str(e)}"
        )
        raise AppException(
            code="ROUTE_UPDATE_CONVERSATION_ERROR",
            message="更新对话失败",
            status_code=500,
        )


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
    try:
        await service.delete_conversation(
            conversation_id=conversation_id,
            user_id=current_user["id"],
        )
        return {"message": "对话已删除"}
    except (
        ValidationError,
        AuthenticationError,
        ConflictError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Error in delete_conversation route | conversation_id={conversation_id} | "
            f"user_id={current_user['id']} | error={str(e)}"
        )
        raise AppException(
            code="ROUTE_DELETE_CONVERSATION_ERROR",
            message="删除对话失败",
            status_code=500,
        )
