"""
记忆功能 API 路由

提供记忆的增删改查、设置管理接口。
"""

from fastapi import APIRouter, Depends
from loguru import logger

from api.deps import CurrentUserId, Database
from core.exceptions import AppException
from schemas.memory import (
    MemoryAddRequest,
    MemoryAddResponse,
    MemoryDeleteAllResponse,
    MemoryDeleteResponse,
    MemoryListResponse,
    MemorySettingsResponse,
    MemorySettingsUpdateRequest,
    MemoryUpdateRequest,
    MemoryUpdateResponse,
)
from services.memory_service import MemoryService

router = APIRouter(prefix="/memories", tags=["记忆"])


def get_memory_service(db: Database) -> MemoryService:
    """获取记忆服务实例"""
    return MemoryService(db)


# ===== 记忆设置 =====


@router.get("/settings", response_model=MemorySettingsResponse, summary="获取记忆设置")
async def get_memory_settings(
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    """获取当前用户的记忆功能设置"""
    try:
        return await service.get_settings(current_user_id)
    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Error in get_memory_settings | user_id={current_user_id} | error={e}"
        )
        raise AppException(
            code="ROUTE_GET_MEMORY_SETTINGS_ERROR",
            message="获取记忆设置失败",
            status_code=500,
        )


@router.put("/settings", response_model=MemorySettingsResponse, summary="更新记忆设置")
async def update_memory_settings(
    body: MemorySettingsUpdateRequest,
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    """更新当前用户的记忆功能设置（开关、保留天数）"""
    try:
        return await service.update_settings(
            current_user_id,
            memory_enabled=body.memory_enabled,
            retention_days=body.retention_days,
        )
    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Error in update_memory_settings | user_id={current_user_id} | error={e}"
        )
        raise AppException(
            code="ROUTE_UPDATE_MEMORY_SETTINGS_ERROR",
            message="更新记忆设置失败",
            status_code=500,
        )


# ===== 记忆 CRUD =====


@router.get("", response_model=MemoryListResponse, summary="获取记忆列表")
async def get_memories(
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    """获取当前用户的所有记忆"""
    try:
        memories = await service.get_all_memories(current_user_id)
        return {"memories": memories, "total": len(memories)}
    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Error in get_memories | user_id={current_user_id} | error={e}"
        )
        raise AppException(
            code="ROUTE_GET_MEMORIES_ERROR",
            message="获取记忆列表失败",
            status_code=500,
        )


@router.post("", response_model=MemoryAddResponse, summary="添加记忆")
async def add_memory(
    body: MemoryAddRequest,
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    """手动添加一条记忆"""
    try:
        memories = await service.add_memory(
            user_id=current_user_id,
            content=body.content,
            source="manual",
        )
        return {"memories": memories, "count": len(memories)}
    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Error in add_memory | user_id={current_user_id} | error={e}"
        )
        raise AppException(
            code="ROUTE_ADD_MEMORY_ERROR",
            message="添加记忆失败",
            status_code=500,
        )


@router.put("/{memory_id}", response_model=MemoryUpdateResponse, summary="更新记忆")
async def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    """更新一条记忆的内容"""
    try:
        return await service.update_memory(
            memory_id=memory_id,
            content=body.content,
            user_id=current_user_id,
        )
    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Error in update_memory | user_id={current_user_id} | "
            f"memory_id={memory_id} | error={e}"
        )
        raise AppException(
            code="ROUTE_UPDATE_MEMORY_ERROR",
            message="更新记忆失败",
            status_code=500,
        )


@router.delete("/{memory_id}", response_model=MemoryDeleteResponse, summary="删除记忆")
async def delete_memory(
    memory_id: str,
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    """删除一条记忆"""
    try:
        await service.delete_memory(memory_id=memory_id, user_id=current_user_id)
        return {"message": "记忆已删除"}
    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Error in delete_memory | user_id={current_user_id} | "
            f"memory_id={memory_id} | error={e}"
        )
        raise AppException(
            code="ROUTE_DELETE_MEMORY_ERROR",
            message="删除记忆失败",
            status_code=500,
        )


@router.delete("", response_model=MemoryDeleteAllResponse, summary="清空所有记忆")
async def delete_all_memories(
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    """清空当前用户的所有记忆"""
    try:
        await service.delete_all_memories(current_user_id)
        return {"message": "所有记忆已清空"}
    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Error in delete_all_memories | user_id={current_user_id} | error={e}"
        )
        raise AppException(
            code="ROUTE_DELETE_ALL_MEMORIES_ERROR",
            message="清空记忆失败",
            status_code=500,
        )
