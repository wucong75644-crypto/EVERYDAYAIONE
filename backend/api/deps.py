"""
FastAPI 依赖注入

提供通用的依赖注入函数，如获取当前用户、数据库连接等。
"""

from typing import Annotated, Optional

from fastapi import Depends, Header
from supabase import Client
from loguru import logger

from core.database import get_db
from core.exceptions import AuthenticationError, InvalidTokenError
from core.security import decode_access_token
from core.redis import RedisClient
from services.task_limit_service import TaskLimitService


async def get_current_user_id(
    authorization: Annotated[Optional[str], Header()] = None,
) -> str:
    """
    从 Authorization Header 中获取当前用户 ID

    Args:
        authorization: Authorization Header，格式为 "Bearer <token>"

    Returns:
        用户 ID

    Raises:
        AuthenticationError: 未提供认证信息
        InvalidTokenError: Token 无效
    """
    if not authorization:
        raise AuthenticationError("请先登录")

    if not authorization.startswith("Bearer "):
        raise AuthenticationError("认证格式错误")

    token = authorization[7:]  # 去掉 "Bearer " 前缀

    payload = decode_access_token(token)
    user_id = payload.get("sub")

    if not user_id:
        raise InvalidTokenError()

    return user_id


async def get_current_user(
    user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[Client, Depends(get_db)],
) -> dict:
    """
    获取当前登录用户的完整信息

    Args:
        user_id: 用户 ID
        db: 数据库客户端

    Returns:
        用户信息字典

    Raises:
        AuthenticationError: 用户不存在
    """
    response = db.table("users").select(
        "id, phone, nickname, avatar_url, role, credits, status, created_at"
    ).eq("id", user_id).single().execute()

    if not response.data:
        raise AuthenticationError("用户不存在")

    return response.data


async def get_optional_user_id(
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[str]:
    """
    可选的用户认证，未登录时返回 None

    用于同时支持登录和未登录用户的接口。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    try:
        token = authorization[7:]
        payload = decode_access_token(token)
        return payload.get("sub")
    except Exception:
        return None


async def get_task_limit_service() -> Optional[TaskLimitService]:
    """
    获取任务限制服务实例

    如果 Redis 不可用，返回 None（降级处理）
    """
    try:
        redis_client = await RedisClient.get_client()
        return TaskLimitService(redis_client)
    except Exception as e:
        logger.warning(f"TaskLimitService 初始化失败，降级跳过限制 | error={e}")
        return None


# 类型别名，简化依赖注入的使用
CurrentUserId = Annotated[str, Depends(get_current_user_id)]
CurrentUser = Annotated[dict, Depends(get_current_user)]
OptionalUserId = Annotated[Optional[str], Depends(get_optional_user_id)]
Database = Annotated[Client, Depends(get_db)]
TaskLimitSvc = Annotated[Optional[TaskLimitService], Depends(get_task_limit_service)]
