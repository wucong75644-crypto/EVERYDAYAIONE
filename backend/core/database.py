"""
数据库客户端

提供 Supabase 和 Redis 客户端的初始化和获取方法。
"""

from functools import lru_cache
from typing import Optional

import redis
from loguru import logger
from supabase import Client, create_client

from core.config import get_settings


_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """
    获取 Redis 客户端（单例模式）

    Returns:
        Redis 客户端实例
    """
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        logger.info(f"Redis client initialized | host={settings.redis_host}")
    return _redis_client


@lru_cache
def get_supabase_client() -> Client:
    """
    获取 Supabase 客户端（单例模式）

    使用 service_role_key 绕过 RLS 策略，仅限后端服务使用。

    Returns:
        Supabase 客户端实例
    """
    settings = get_settings()
    return create_client(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_service_role_key,
    )


def get_db() -> Client:
    """
    FastAPI 依赖注入用的数据库获取函数

    Returns:
        Supabase 客户端实例
    """
    return get_supabase_client()
