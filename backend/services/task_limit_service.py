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

    @staticmethod
    def _org_prefix(org_id: str | None) -> str:
        return org_id if org_id else "personal"

    def _global_key(self, user_id: str, org_id: str | None = None) -> str:
        """全局任务计数键"""
        return f"task:global:{self._org_prefix(org_id)}:{user_id}"

    def _conversation_key(
        self, user_id: str, conversation_id: str, org_id: str | None = None
    ) -> str:
        """单对话任务计数键"""
        return f"task:conv:{self._org_prefix(org_id)}:{user_id}:{conversation_id}"

    async def check_and_acquire(
        self,
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
    ) -> bool:
        """
        检查限制并获取槽位

        Args:
            user_id: 用户ID
            conversation_id: 对话ID
            org_id: 企业ID（散客为None）

        Returns:
            True 表示获取成功

        Raises:
            TaskQueueFullError: 超过限制时抛出
        """
        try:
            global_key = self._global_key(user_id, org_id)
            conv_key = self._conversation_key(user_id, conversation_id, org_id)

            # 批量读取两个计数（1次往返替代2次）
            async with self.redis.pipeline(transaction=False) as pipe:
                await pipe.get(global_key)
                await pipe.get(conv_key)
                global_count, conv_count = await pipe.execute()

            # 检查全局限制
            if global_count and int(global_count) >= self.global_limit:
                logger.warning(
                    "任务队列已满（全局）",
                    user_id=user_id,
                    current=global_count,
                    limit=self.global_limit
                )
                raise TaskQueueFullError(
                    current_count=int(global_count),
                    max_count=self.global_limit,
                    scope="global",
                )

            # 检查单对话限制
            if conv_count and int(conv_count) >= self.conversation_limit:
                logger.warning(
                    "任务队列已满（单对话）",
                    user_id=user_id,
                    conversation_id=conversation_id,
                    current=conv_count,
                    limit=self.conversation_limit
                )
                raise TaskQueueFullError(
                    current_count=int(conv_count),
                    max_count=self.conversation_limit,
                    scope="conversation",
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
        conversation_id: str,
        org_id: str | None = None,
    ) -> None:
        """
        释放槽位

        Args:
            user_id: 用户ID
            conversation_id: 对话ID
            org_id: 企业ID（散客为None）
        """
        try:
            global_key = self._global_key(user_id, org_id)
            conv_key = self._conversation_key(user_id, conversation_id, org_id)

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
        conversation_id: Optional[str] = None,
        org_id: str | None = None,
    ) -> dict:
        """
        获取活跃任务数量

        Args:
            user_id: 用户ID
            conversation_id: 对话ID（可选）
            org_id: 企业ID（散客为None）

        Returns:
            包含 global 和 conversation 计数的字典
        """
        global_count = await self.redis.get(self._global_key(user_id, org_id)) or 0

        conv_count = 0
        if conversation_id:
            conv_count = await self.redis.get(
                self._conversation_key(user_id, conversation_id, org_id)
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
        conversation_id: str,
        org_id: str | None = None,
    ) -> bool:
        """
        检查是否可以启动新任务（不抛异常）

        Args:
            user_id: 用户ID
            conversation_id: 对话ID
            org_id: 企业ID（散客为None）

        Returns:
            True 表示可以启动
        """
        try:
            counts = await self.get_active_count(user_id, conversation_id, org_id)
            return (
                counts["global"] < self.global_limit and
                counts["conversation"] < self.conversation_limit
            )
        except Exception as e:
            logger.error("检查任务限制失败", error=str(e))
            # 降级：Redis 不可用时允许执行
            return True
