"""
数据库客户端

提供 PostgreSQL（LocalDB）和 Redis 客户端的初始化和获取方法。
"""

import ssl
from typing import Any, Optional

import certifi
import redis
from loguru import logger

from core.config import get_settings


_redis_client: Optional[redis.Redis] = None
_local_db_client = None
_async_db_client = None
_worker_db_client = None
_async_worker_db_client = None
_runtime_admin_db_client = None


def get_redis_client() -> redis.Redis:
    """获取 Redis 客户端（单例模式）"""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()

        connection_kwargs = {
            "decode_responses": True,
        }

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


def get_db():
    """获取数据库客户端（单例模式，LocalDBClient）"""
    global _local_db_client
    if _local_db_client is None:
        from core.local_db import LocalDBClient
        settings = get_settings()
        _local_db_client = LocalDBClient(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
        )
        logger.info("数据库连接池已创建 | LocalDB")
    return _local_db_client


def close_db() -> None:
    """关闭 runtime 同步数据库连接池。"""
    global _local_db_client
    if _local_db_client is not None:
        _local_db_client.close()
        _local_db_client = None


async def get_async_db():
    """获取异步数据库客户端（单例模式，AsyncLocalDBClient）

    必须在 async 上下文中调用。首次调用会创建连接池并 open。
    """
    global _async_db_client
    if _async_db_client is None:
        from core.local_db import AsyncLocalDBClient
        settings = get_settings()
        _async_db_client = AsyncLocalDBClient(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
        )
        await _async_db_client.open()
        logger.info("异步数据库连接池已创建 | AsyncLocalDB")
    return _async_db_client


async def close_async_db() -> None:
    """关闭异步数据库连接池（应用关闭时调用）"""
    global _async_db_client
    if _async_db_client is not None:
        await _async_db_client.close()
        _async_db_client = None


def get_worker_db() -> Any:
    """获取独立 Worker 同步数据库客户端；禁止回退 runtime URL。"""
    global _worker_db_client
    if _worker_db_client is None:
        from core.local_db import LocalDBClient

        settings = get_settings()
        if not settings.worker_database_url:
            raise RuntimeError("WORKER_DATABASE_URL_REQUIRED")
        _worker_db_client = LocalDBClient(
            settings.worker_database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
        )
        logger.info("Worker 数据库连接池已创建 | LocalDB")
    return _worker_db_client


async def get_async_worker_db(
    database_url: str | None = None, *, min_size: int | None = None,
    max_size: int | None = None,
) -> Any:
    """获取独立 Worker 异步数据库客户端；禁止回退 runtime URL。"""
    global _async_worker_db_client
    if _async_worker_db_client is None:
        from core.local_db import AsyncLocalDBClient

        settings = None if database_url is not None else get_settings()
        resolved_url = (
            database_url if database_url is not None
            else settings.worker_database_url
        )
        if not resolved_url:
            raise RuntimeError("WORKER_DATABASE_URL_REQUIRED")
        _async_worker_db_client = AsyncLocalDBClient(
            resolved_url,
            min_size=min_size or (settings.db_pool_min if settings else 1),
            max_size=max_size or (settings.db_pool_max if settings else 2),
        )
        await _async_worker_db_client.open()
        logger.info("Worker 异步数据库连接池已创建 | AsyncLocalDB")
    return _async_worker_db_client


def close_worker_db() -> None:
    """关闭 Worker 同步数据库连接池。"""
    global _worker_db_client
    if _worker_db_client is not None:
        _worker_db_client.close()
        _worker_db_client = None


async def close_async_worker_db() -> None:
    """关闭 Worker 异步数据库连接池。"""
    global _async_worker_db_client
    if _async_worker_db_client is not None:
        await _async_worker_db_client.close()
        _async_worker_db_client = None


def get_runtime_admin_db() -> Any:
    """Get the narrow runtime-admin connection; never reuse the web role."""
    global _runtime_admin_db_client
    if _runtime_admin_db_client is None:
        from core.local_db import LocalDBClient

        settings = get_settings()
        if not settings.runtime_admin_database_url:
            raise RuntimeError("RUNTIME_ADMIN_DATABASE_URL_REQUIRED")
        _runtime_admin_db_client = LocalDBClient(
            settings.runtime_admin_database_url,
            min_size=1,
            max_size=min(2, settings.db_pool_max),
        )
    return _runtime_admin_db_client


def close_runtime_admin_db() -> None:
    global _runtime_admin_db_client
    if _runtime_admin_db_client is not None:
        _runtime_admin_db_client.close()
        _runtime_admin_db_client = None
