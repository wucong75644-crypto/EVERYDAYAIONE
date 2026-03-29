"""
task_limit_service 单元测试

测试 TaskLimitService 的核心功能：
- check_and_acquire: pipeline 批量读取 + 限制检查 + 原子递增
- release: 释放槽位
- get_active_count: 获取计数
- can_start_task: 无异常检查
- Redis 故障降级
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ============ Fixtures ============


@pytest.fixture
def mock_redis():
    """Mock Redis 客户端（支持 pipeline context manager）"""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    # pipeline 返回 async context manager
    pipe = AsyncMock()
    pipe.get = AsyncMock()
    pipe.incr = AsyncMock()
    pipe.decr = AsyncMock()
    pipe.expire = AsyncMock()
    pipe.execute = AsyncMock(return_value=[None, None])

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=pipe)
    ctx.__aexit__ = AsyncMock(return_value=False)
    redis.pipeline = MagicMock(return_value=ctx)

    return redis, pipe


@pytest.fixture
def service(mock_redis):
    """创建 TaskLimitService 实例"""
    redis_client, _ = mock_redis
    with patch("services.task_limit_service.settings") as mock_settings:
        mock_settings.rate_limit_global_tasks = 15
        mock_settings.rate_limit_conversation_tasks = 5
        from services.task_limit_service import TaskLimitService
        svc = TaskLimitService(redis_client)
    return svc


# ============ check_and_acquire 测试 ============


class TestCheckAndAcquire:
    """check_and_acquire: pipeline 批量读取 + 限制检查"""

    @pytest.mark.asyncio
    async def test_success_under_limits(self, service, mock_redis):
        """计数均在限制内时获取成功"""
        _, pipe = mock_redis
        # pipeline 返回 [None, None] → 计数为 0
        pipe.execute.return_value = [None, None]

        result = await service.check_and_acquire("user1", "conv1")

        assert result is True
        # 应调用 incr 递增两个 key
        assert pipe.incr.await_count == 2

    @pytest.mark.asyncio
    async def test_global_limit_exceeded(self, service, mock_redis):
        """全局任务数达到上限时抛出 TaskQueueFullError"""
        _, pipe = mock_redis
        pipe.execute.return_value = [b"15", b"0"]

        from core.exceptions import TaskQueueFullError

        with pytest.raises(TaskQueueFullError) as exc_info:
            await service.check_and_acquire("user1", "conv1")

        assert exc_info.value.details["scope"] == "global"
        assert exc_info.value.details["current_count"] == 15
        assert exc_info.value.details["max_count"] == 15

    @pytest.mark.asyncio
    async def test_conversation_limit_exceeded(self, service, mock_redis):
        """单对话任务数达到上限时抛出 TaskQueueFullError"""
        _, pipe = mock_redis
        pipe.execute.return_value = [b"3", b"5"]

        from core.exceptions import TaskQueueFullError

        with pytest.raises(TaskQueueFullError) as exc_info:
            await service.check_and_acquire("user1", "conv1")

        assert exc_info.value.details["scope"] == "conversation"
        assert exc_info.value.details["current_count"] == 5
        assert exc_info.value.details["max_count"] == 5

    @pytest.mark.asyncio
    async def test_redis_error_degrades_to_allow(self, service, mock_redis):
        """Redis 异常时降级允许执行"""
        redis_client, _ = mock_redis
        # pipeline 创建直接抛异常
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("Redis down"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        redis_client.pipeline = MagicMock(return_value=ctx)

        result = await service.check_and_acquire("user1", "conv1")

        assert result is True


# ============ release 测试 ============


class TestRelease:
    """release: 释放槽位"""

    @pytest.mark.asyncio
    async def test_release_decrements_both_keys(self, service, mock_redis):
        """释放时递减全局和对话计数"""
        _, pipe = mock_redis

        await service.release("user1", "conv1")

        assert pipe.decr.await_count == 2

    @pytest.mark.asyncio
    async def test_release_ignores_redis_error(self, service, mock_redis):
        """Redis 异常时静默忽略"""
        redis_client, _ = mock_redis
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("Redis down"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        redis_client.pipeline = MagicMock(return_value=ctx)

        # 不应抛异常
        await service.release("user1", "conv1")


# ============ get_active_count / can_start_task 测试 ============


class TestGetActiveCount:
    """get_active_count 和 can_start_task"""

    @pytest.mark.asyncio
    async def test_get_active_count_with_conversation(self, service, mock_redis):
        """同时获取全局和对话计数"""
        redis_client, _ = mock_redis
        redis_client.get = AsyncMock(side_effect=[b"3", b"2"])

        result = await service.get_active_count("user1", "conv1")

        assert result["global"] == 3
        assert result["conversation"] == 2
        assert result["global_limit"] == 15
        assert result["conversation_limit"] == 5

    @pytest.mark.asyncio
    async def test_can_start_task_true(self, service, mock_redis):
        """未超限时返回 True"""
        redis_client, _ = mock_redis
        redis_client.get = AsyncMock(side_effect=[b"5", b"2"])

        result = await service.can_start_task("user1", "conv1")

        assert result is True

    @pytest.mark.asyncio
    async def test_can_start_task_false_when_at_limit(self, service, mock_redis):
        """达到限制时返回 False"""
        redis_client, _ = mock_redis
        redis_client.get = AsyncMock(side_effect=[b"15", b"2"])

        result = await service.can_start_task("user1", "conv1")

        assert result is False


# ============ Key 生成测试 ============


class TestKeyGeneration:
    """Redis key 格式验证"""

    def test_global_key_format(self, service):
        # 散客（无 org_id）
        assert service._global_key("user_abc") == "task:global:personal:user_abc"
        # 企业用户
        assert service._global_key("user_abc", org_id="org_1") == "task:global:org_1:user_abc"

    def test_conversation_key_format(self, service):
        # 散客
        assert service._conversation_key("user_abc", "conv_123") == "task:conv:personal:user_abc:conv_123"
        # 企业用户
        assert service._conversation_key("user_abc", "conv_123", org_id="org_1") == "task:conv:org_1:user_abc:conv_123"
