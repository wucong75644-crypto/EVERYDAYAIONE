"""电商图 AI 帮写的跨进程用户级频率限制。"""

from loguru import logger

from core.exceptions import AppException
from core.redis import RedisClient


_LIMIT = 5
_WINDOW_SECONDS = 60
_INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RequirementAssistRateLimiter:
    """使用 Redis 原子计数，在多个 Uvicorn worker 间共享限流状态。"""

    async def check(self, user_id: str) -> None:
        key = f"rate:requirement-assist:{user_id}"
        try:
            redis = await RedisClient.get_client()
            current = await redis.eval(_INCREMENT_SCRIPT, 1, key, _WINDOW_SECONDS)
        except Exception as exc:
            logger.warning(
                f"Requirement assist rate limit unavailable, fail open | "
                f"user_id={user_id} | error_type={type(exc).__name__}"
            )
            return
        if int(current) > _LIMIT:
            raise AppException(
                "REQUIREMENT_ASSIST_RATE_LIMITED",
                "AI帮写请求过于频繁，请稍后重试",
                429,
                details={"retry_after": _WINDOW_SECONDS},
            )
