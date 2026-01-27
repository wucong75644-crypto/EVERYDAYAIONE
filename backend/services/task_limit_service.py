"""
任务限制服务

提供基于 Redis 的任务并发限制功能：
- 全局任务限制（默认15个）
- 单对话任务限制（默认5个）
"""
from typing import Optional

from redis.asyncio import Redis
from loguru import logger

from core.config import settings
from core.exceptions import TaskQueueFullError


class TaskLimitService:
    """任务限制服务"""

    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self.global_limit = settings.rate_limit_global_tasks
        self.conversation_limit = settings.rate_limit_conversation_tasks

    def _global_key(self, user_id: str) -> str:
        """全局任务计数键"""
        return f"task:global:{user_id}"

    def _conversation_key(self, user_id: str, conversation_id: str) -> str:
        """单对话任务计数键"""
        return f"task:conv:{user_id}:{conversation_id}"

    async def check_and_acquire(
        self,
        user_id: str,
        conversation_id: str
    ) -> bool:
        """
        检查限制并获取槽位

        Args:
            user_id: 用户ID
            conversation_id: 对话ID

        Returns:
            True 表示获取成功

        Raises:
            TaskQueueFullError: 超过限制时抛出
        """
        try:
            global_key = self._global_key(user_id)
            conv_key = self._conversation_key(user_id, conversation_id)

            # 检查全局限制
            global_count = await self.redis.get(global_key)
            if global_count and int(global_count) >= self.global_limit:
                logger.warning(
                    "任务队列已满（全局）",
                    user_id=user_id,
                    current=global_count,
                    limit=self.global_limit
                )
                raise TaskQueueFullError(
                    f"任务队列已满，最多同时执行 {self.global_limit} 个任务"
                )

            # 检查单对话限制
            conv_count = await self.redis.get(conv_key)
            if conv_count and int(conv_count) >= self.conversation_limit:
                logger.warning(
                    "任务队列已满（单对话）",
                    user_id=user_id,
                    conversation_id=conversation_id,
                    current=conv_count,
                    limit=self.conversation_limit
                )
                raise TaskQueueFullError(
                    f"当前对话任务队列已满，最多同时执行 {self.conversation_limit} 个任务"
                )

            # 原子递增（使用 pipeline 保证原子性）
            async with self.redis.pipeline() as pipe:
                await pipe.incr(global_key)
                await pipe.expire(global_key, 3600)  # 1小时过期
                await pipe.incr(conv_key)
                await pipe.expire(conv_key, 3600)
                await pipe.execute()

            logger.debug(
                "获取任务槽位成功",
                user_id=user_id,
                conversation_id=conversation_id
            )
            return True
        except TaskQueueFullError:
            raise
        except Exception as e:
            logger.warning(f"任务限制检查失败，降级允许执行 | error={e}")
            return True

    async def release(
        self,
        user_id: str,
        conversation_id: str
    ) -> None:
        """
        释放槽位

        Args:
            user_id: 用户ID
            conversation_id: 对话ID
        """
        try:
            global_key = self._global_key(user_id)
            conv_key = self._conversation_key(user_id, conversation_id)

            async with self.redis.pipeline() as pipe:
                await pipe.decr(global_key)
                await pipe.decr(conv_key)
                await pipe.execute()

            logger.debug(
                "释放任务槽位",
                user_id=user_id,
                conversation_id=conversation_id
            )
        except Exception as e:
            logger.warning(f"释放任务槽位失败，忽略 | error={e}")

    async def get_active_count(
        self,
        user_id: str,
        conversation_id: Optional[str] = None
    ) -> dict:
        """
        获取活跃任务数量

        Args:
            user_id: 用户ID
            conversation_id: 对话ID（可选）

        Returns:
            包含 global 和 conversation 计数的字典
        """
        global_count = await self.redis.get(self._global_key(user_id)) or 0

        conv_count = 0
        if conversation_id:
            conv_count = await self.redis.get(
                self._conversation_key(user_id, conversation_id)
            ) or 0

        return {
            "global": int(global_count),
            "global_limit": self.global_limit,
            "conversation": int(conv_count),
            "conversation_limit": self.conversation_limit
        }

    async def can_start_task(
        self,
        user_id: str,
        conversation_id: str
    ) -> bool:
        """
        检查是否可以启动新任务（不抛异常）

        Args:
            user_id: 用户ID
            conversation_id: 对话ID

        Returns:
            True 表示可以启动
        """
        try:
            counts = await self.get_active_count(user_id, conversation_id)
            return (
                counts["global"] < self.global_limit and
                counts["conversation"] < self.conversation_limit
            )
        except Exception as e:
            logger.error("检查任务限制失败", error=str(e))
            # 降级：Redis 不可用时允许执行
            return True
