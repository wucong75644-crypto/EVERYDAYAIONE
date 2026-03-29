"""
Redis 连接管理模块

提供 Redis 连接池管理、分布式锁、任务队列（Sorted Set）等功能。
"""
from typing import Optional
import time
import uuid

from redis.asyncio import Redis
from loguru import logger

from core.config import settings


async def get_redis() -> Optional[Redis]:
    """获取 Redis 客户端的便捷函数"""
    try:
        return await RedisClient.get_client()
    except Exception as e:
        logger.debug(f"Redis 连接获取失败 | error={e}")
        return None


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
            await cls._instance.aclose()
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

    # ── 任务队列（Sorted Set）──────────────────────────

    @classmethod
    async def enqueue_task(
        cls, queue_key: str, task_id: str, score: float | None = None,
    ) -> bool:
        """原子入队（ZADD NX），已存在则跳过。

        Args:
            queue_key: 队列名（如 erp_tasks）
            task_id: 任务唯一标识（如 "org_id:sync_type"）
            score: 优先级分数，越小越先被取出。默认用当前时间戳。

        Returns:
            True=新增成功，False=已存在被跳过
        """
        client = await cls.get_client()
        if score is None:
            score = time.time()
        added = await client.zadd(queue_key, {task_id: score}, nx=True)
        return added > 0

    @classmethod
    async def dequeue_task(cls, queue_key: str) -> tuple[str, float] | None:
        """原子取出分数最小（最紧急）的任务（ZPOPMIN）。

        Returns:
            (task_id, score) 或 None（队列为空）
        """
        client = await cls.get_client()
        result = await client.zpopmin(queue_key, count=1)
        if not result:
            return None
        member, score = result[0]
        return (member, score)

    @classmethod
    async def queue_size(cls, queue_key: str) -> int:
        """返回队列中待处理任务数"""
        client = await cls.get_client()
        return await client.zcard(queue_key)

    # ── 并发计数器（限流用）────────────────────────────

    @classmethod
    async def incr_with_ttl(cls, key: str, ttl: int = 300) -> int:
        """原子递增计数器，首次创建时设置 TTL（防泄漏）。

        Returns:
            递增后的值
        """
        client = await cls.get_client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)  # 仅首次设置过期
        results = await pipe.execute()
        return results[0]

    @classmethod
    async def decr_floor(cls, key: str) -> int:
        """递减计数器，最低为 0。

        Returns:
            递减后的值
        """
        client = await cls.get_client()
        lua_script = """
        local val = redis.call("decr", KEYS[1])
        if val < 0 then
            redis.call("set", KEYS[1], 0)
            return 0
        end
        return val
        """
        return await client.eval(lua_script, 1, key)

    @classmethod
    async def try_throttle(cls, key: str, ttl: int = 30) -> bool:
        """节流：SET NX EX，TTL 内只允许一次。

        Returns:
            True=获得执行权，False=节流中应跳过
        """
        client = await cls.get_client()
        result = await client.set(key, "1", nx=True, ex=ttl)
        return result is not None
