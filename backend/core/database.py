"""
数据库客户端

提供 Supabase 和 Redis 客户端的初始化和获取方法。
"""

import ssl
from functools import lru_cache
from typing import Optional

import certifi
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

        # 构建连接参数
        connection_kwargs = {
            "decode_responses": True,
        }

        # 如果启用 SSL，使用 certifi 提供的 CA 证书包
        if settings.redis_ssl:
            connection_kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
            connection_kwargs["ssl_ca_certs"] = certifi.where()

        _redis_client = redis.from_url(
            settings.redis_url,
            **connection_kwargs,
        )
        logger.info(
            f"Redis client initialized | host={settings.redis_host} | "
            f"ssl={settings.redis_ssl}"
        )
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
