"""
任务限制服务

基于 Redis SET 的任务并发限制：
- 全局任务限制（默认15个）
- 单对话任务限制（默认5个）

使用 SET 存储活跃 slot_id，替代 INCR/DECR 计数器：
- 幂等：重复 release 不会变负数
- 可审计：能看到哪些 slot 在占用
- 可自愈：超时清理时直接 SREM
"""

import uuid
from typing import Optional

from redis.asyncio import Redis
from loguru import logger

from core.config import settings
from core.exceptions import TaskQueueFullError

# SET key 过期时间（秒），兜底防止永久残留
_SET_TTL = 3600


class TaskLimitService:
    """任务限制服务（基于 Redis SET）"""

    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self.global_limit = settings.rate_limit_global_tasks
        self.conversation_limit = settings.rate_limit_conversation_tasks

    @staticmethod
    def _org_prefix(org_id: str | None) -> str:
        return org_id if org_id else "personal"

    def _global_key(self, user_id: str, org_id: str | None = None) -> str:
        """全局活跃任务 SET 键"""
        return f"task:active:{self._org_prefix(org_id)}:{user_id}"

    def _conversation_key(
        self, user_id: str, conversation_id: str, org_id: str | None = None
    ) -> str:
        """单对话活跃任务 SET 键"""
        return f"task:conv_active:{self._org_prefix(org_id)}:{user_id}:{conversation_id}"

    async def check_and_acquire(
        self,
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
    ) -> str:
        """
        检查限制并获取槽位

        Returns:
            slot_id: 槽位标识（调用方需保存，release 时传入）

        Raises:
            TaskQueueFullError: 超过限制时抛出
        """
        try:
            global_key = self._global_key(user_id, org_id)
            conv_key = self._conversation_key(user_id, conversation_id, org_id)

            # 批量读取两个 SET 的大小
            async with self.redis.pipeline(transaction=False) as pipe:
                await pipe.scard(global_key)
                await pipe.scard(conv_key)
                global_count, conv_count = await pipe.execute()

            # 检查全局限制
            if global_count >= self.global_limit:
                logger.warning(
                    f"任务队列已满（全局） | user_id={user_id} | "
                    f"current={global_count} | limit={self.global_limit}"
                )
                raise TaskQueueFullError(
                    current_count=global_count,
                    max_count=self.global_limit,
                    scope="global",
                )

            # 检查单对话限制
            if conv_count >= self.conversation_limit:
                logger.warning(
                    f"任务队列已满（单对话） | user_id={user_id} | "
                    f"conversation_id={conversation_id} | "
                    f"current={conv_count} | limit={self.conversation_limit}"
                )
                raise TaskQueueFullError(
                    current_count=conv_count,
                    max_count=self.conversation_limit,
                    scope="conversation",
                )

            # 生成唯一槽位 ID 并加入 SET
            slot_id = str(uuid.uuid4())
            async with self.redis.pipeline() as pipe:
                await pipe.sadd(global_key, slot_id)
                await pipe.expire(global_key, _SET_TTL)
                await pipe.sadd(conv_key, slot_id)
                await pipe.expire(conv_key, _SET_TTL)
                await pipe.execute()

            logger.debug(
                f"获取任务槽位成功 | user_id={user_id} | "
                f"conversation_id={conversation_id} | slot_id={slot_id}"
            )
            return slot_id
        except TaskQueueFullError:
            raise
        except Exception as e:
            logger.warning(f"任务限制检查失败，降级允许执行 | error={e}")
            return str(uuid.uuid4())

    async def release(
        self,
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
        slot_id: str | None = None,
    ) -> None:
        """
        释放槽位

        Args:
            user_id: 用户ID
            conversation_id: 对话ID
            org_id: 企业ID（散客为None）
            slot_id: 槽位标识（acquire 时返回的）
        """
        if not slot_id:
            logger.debug("release called without slot_id, skipping")
            return

        try:
            global_key = self._global_key(user_id, org_id)
            conv_key = self._conversation_key(user_id, conversation_id, org_id)

            async with self.redis.pipeline() as pipe:
                await pipe.srem(global_key, slot_id)
                await pipe.srem(conv_key, slot_id)
                await pipe.execute()

            logger.debug(
                f"释放任务槽位 | user_id={user_id} | "
                f"conversation_id={conversation_id} | slot_id={slot_id}"
            )
        except Exception as e:
            logger.warning(f"释放任务槽位失败，忽略 | slot_id={slot_id} | error={e}")

    async def get_active_count(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        org_id: str | None = None,
    ) -> dict:
        """获取活跃任务数量"""
        global_count = await self.redis.scard(self._global_key(user_id, org_id))

        conv_count = 0
        if conversation_id:
            conv_count = await self.redis.scard(
                self._conversation_key(user_id, conversation_id, org_id)
            )

        return {
            "global": global_count,
            "global_limit": self.global_limit,
            "conversation": conv_count,
            "conversation_limit": self.conversation_limit,
        }

    async def can_start_task(
        self,
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
    ) -> bool:
        """检查是否可以启动新任务（不抛异常）"""
        try:
            counts = await self.get_active_count(user_id, conversation_id, org_id)
            return (
                counts["global"] < self.global_limit
                and counts["conversation"] < self.conversation_limit
            )
        except Exception as e:
            logger.error(f"检查任务限制失败 | error={e}")
            return True


def extract_slot_id(task: dict) -> str | None:
    """从 task 的 request_params 中提取 _task_slot_id（公共函数，多处释放路径复用）"""
    import json
    params = task.get("request_params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            return None
    return params.get("_task_slot_id")


async def release_task_slot(task: dict) -> None:
    """从 task 提取 slot_id 并释放槽位（公共函数，多处释放路径复用）"""
    slot_id = extract_slot_id(task)
    if not slot_id:
        return
    try:
        from api.deps import get_task_limit_service
        service = await get_task_limit_service()
        if service:
            await service.release(
                task["user_id"],
                task["conversation_id"],
                org_id=task.get("org_id"),
                slot_id=slot_id,
            )
    except Exception as e:
        logger.debug(f"Task slot release skipped | slot_id={slot_id} | error={e}")
