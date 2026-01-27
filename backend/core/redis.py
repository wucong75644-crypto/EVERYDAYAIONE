"""
Redis 连接管理模块

提供 Redis 连接池管理、分布式锁等功能。
"""
from typing import Optional
import uuid

from redis.asyncio import Redis
from loguru import logger

from core.config import settings


class RedisClient:
    """Redis 连接管理（单例模式）"""

    _instance: Optional[Redis] = None

    @classmethod
    async def get_client(cls) -> Redis:
        """获取 Redis 客户端"""
        if cls._instance is None:
            cls._instance = Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
            )
            logger.info("Redis 连接已建立", url=settings.redis_url.split("@")[-1])
        return cls._instance

    @classmethod
    async def close(cls) -> None:
        """关闭 Redis 连接"""
        if cls._instance:
            await cls._instance.close()
            cls._instance = None
            logger.info("Redis 连接已关闭")

    @classmethod
    async def health_check(cls) -> bool:
        """健康检查"""
        try:
            client = await cls.get_client()
            await client.ping()
            return True
        except Exception as e:
            logger.error("Redis 健康检查失败", error=str(e))
            return False

    @classmethod
    async def acquire_lock(
        cls,
        key: str,
        timeout: int = 10
    ) -> Optional[str]:
        """
        获取分布式锁

        Args:
            key: 锁的键名
            timeout: 锁超时时间（秒）

        Returns:
            成功返回锁 token，失败返回 None
        """
        client = await cls.get_client()
        token = str(uuid.uuid4())
        acquired = await client.set(
            f"lock:{key}",
            token,
            nx=True,
            ex=timeout
        )
        if acquired:
            logger.debug("获取分布式锁成功", key=key, timeout=timeout)
        return token if acquired else None

    @classmethod
    async def release_lock(cls, key: str, token: str) -> bool:
        """
        释放分布式锁（使用 Lua 脚本保证原子性）

        Args:
            key: 锁的键名
            token: 获取锁时返回的 token

        Returns:
            是否成功释放
        """
        client = await cls.get_client()
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = await client.eval(lua_script, 1, f"lock:{key}", token)
        if result == 1:
            logger.debug("释放分布式锁成功", key=key)
        return result == 1

    @classmethod
    async def extend_lock(cls, key: str, token: str, timeout: int = 10) -> bool:
        """
        延长锁的过期时间

        Args:
            key: 锁的键名
            token: 获取锁时返回的 token
            timeout: 新的超时时间（秒）

        Returns:
            是否成功延长
        """
        client = await cls.get_client()
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        result = await client.eval(lua_script, 1, f"lock:{key}", token, timeout)
        return result == 1
