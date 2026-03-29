"""
RedisClient 队列操作单元测试
覆盖：enqueue_task、dequeue_task、queue_size、incr_with_ttl、decr_floor、try_throttle
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


def _mock_redis_client():
    """创建 mock Redis 客户端"""
    mock_client = AsyncMock()
    return mock_client


# ── enqueue_task ─────────────────────────────────────

class TestEnqueueTask:

    @pytest.mark.asyncio
    async def test_enqueue_new_task(self):
        mock = _mock_redis_client()
        mock.zadd = AsyncMock(return_value=1)  # 1 = added

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.enqueue_task("erp_tasks", "org1:product", 100.0)

        assert result is True
        mock.zadd.assert_called_once_with("erp_tasks", {"org1:product": 100.0}, nx=True)

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_skipped(self):
        mock = _mock_redis_client()
        mock.zadd = AsyncMock(return_value=0)  # 0 = already exists

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.enqueue_task("erp_tasks", "org1:product", 100.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_enqueue_default_score_is_timestamp(self):
        mock = _mock_redis_client()
        mock.zadd = AsyncMock(return_value=1)

        before = time.time()
        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            await RedisClient.enqueue_task("erp_tasks", "org1:product")
        after = time.time()

        call_args = mock.zadd.call_args[0]
        score = list(call_args[1].values())[0]
        assert before <= score <= after


# ── dequeue_task ─────────────────────────────────────

class TestDequeueTask:

    @pytest.mark.asyncio
    async def test_dequeue_returns_task(self):
        mock = _mock_redis_client()
        mock.zpopmin = AsyncMock(return_value=[("org1:product", 100.0)])

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.dequeue_task("erp_tasks")

        assert result == ("org1:product", 100.0)

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_none(self):
        mock = _mock_redis_client()
        mock.zpopmin = AsyncMock(return_value=[])

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.dequeue_task("erp_tasks")

        assert result is None


# ── queue_size ───────────────────────────────────────

class TestQueueSize:

    @pytest.mark.asyncio
    async def test_returns_count(self):
        mock = _mock_redis_client()
        mock.zcard = AsyncMock(return_value=42)

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.queue_size("erp_tasks")

        assert result == 42


# ── incr_with_ttl ────────────────────────────────────

class TestIncrWithTtl:

    @pytest.mark.asyncio
    async def test_returns_incremented_value(self):
        mock = _mock_redis_client()
        mock_pipe = MagicMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[5, True])
        mock.pipeline = MagicMock(return_value=mock_pipe)

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.incr_with_ttl("key", ttl=300)

        assert result == 5
        mock_pipe.incr.assert_called_once_with("key")
        mock_pipe.expire.assert_called_once_with("key", 300, nx=True)


# ── decr_floor ───────────────────────────────────────

class TestDecrFloor:

    @pytest.mark.asyncio
    async def test_returns_decremented_value(self):
        mock = _mock_redis_client()
        mock.eval = AsyncMock(return_value=3)

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.decr_floor("key")

        assert result == 3

    @pytest.mark.asyncio
    async def test_floor_at_zero(self):
        mock = _mock_redis_client()
        mock.eval = AsyncMock(return_value=0)

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.decr_floor("key")

        assert result == 0


# ── try_throttle ─────────────────────────────────────

class TestTryThrottle:

    @pytest.mark.asyncio
    async def test_first_call_succeeds(self):
        mock = _mock_redis_client()
        mock.set = AsyncMock(return_value=True)

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.try_throttle("key", ttl=30)

        assert result is True
        mock.set.assert_called_once_with("key", "1", nx=True, ex=30)

    @pytest.mark.asyncio
    async def test_throttled_returns_false(self):
        mock = _mock_redis_client()
        mock.set = AsyncMock(return_value=None)  # NX fails

        with patch("core.redis.RedisClient.get_client", new_callable=AsyncMock, return_value=mock):
            from core.redis import RedisClient
            result = await RedisClient.try_throttle("key", ttl=30)

        assert result is False
