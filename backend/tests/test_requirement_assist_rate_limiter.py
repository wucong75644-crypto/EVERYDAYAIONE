"""AI 帮写 Redis 用户级限流测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from core.exceptions import AppException
from services.agent.image.requirement_assist_rate_limiter import RequirementAssistRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_first_five_requests() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 5
    with patch(
        "services.agent.image.requirement_assist_rate_limiter.RedisClient.get_client",
        AsyncMock(return_value=redis),
    ):
        await RequirementAssistRateLimiter().check("user-1")
    assert redis.eval.await_args.args[2] == "rate:requirement-assist:user-1"


@pytest.mark.asyncio
async def test_rate_limiter_rejects_sixth_request() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 6
    with patch(
        "services.agent.image.requirement_assist_rate_limiter.RedisClient.get_client",
        AsyncMock(return_value=redis),
    ):
        with pytest.raises(AppException) as exc:
            await RequirementAssistRateLimiter().check("user-1")
    assert exc.value.code == "REQUIREMENT_ASSIST_RATE_LIMITED"
    assert exc.value.status_code == 429
    assert exc.value.details == {"retry_after": 60}


@pytest.mark.asyncio
async def test_rate_limiter_fails_open_when_redis_is_unavailable() -> None:
    with patch(
        "services.agent.image.requirement_assist_rate_limiter.RedisClient.get_client",
        AsyncMock(side_effect=ConnectionError),
    ):
        await RequirementAssistRateLimiter().check("user-1")
