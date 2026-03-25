"""
数据库客户端

提供 PostgreSQL（LocalDB）和 Redis 客户端的初始化和获取方法。
"""

import ssl
from typing import Optional

import certifi
import redis
from loguru import logger

from core.config import get_settings


_redis_client: Optional[redis.Redis] = None
_local_db_client = None


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
