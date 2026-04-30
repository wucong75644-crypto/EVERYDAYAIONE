"""
task_limit_service 单元测试

测试 TaskLimitService 的核心功能（基于 Redis SET）：
- check_and_acquire: SCARD 检查 + SADD 获取，返回 slot_id
- release: SREM 释放指定 slot_id
- get_active_count: SCARD 获取计数
- can_start_task: 无异常检查
- Redis 故障降级
- 幂等性：重复 release 不报错
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import ModuleType

# Mock redis 模块（本地测试环境可能未安装）
_redis_mock = ModuleType("redis")
_redis_asyncio_mock = ModuleType("redis.asyncio")
_redis_asyncio_mock.Redis = MagicMock
_redis_mock.asyncio = _redis_asyncio_mock
sys.modules.setdefault("redis", _redis_mock)
sys.modules.setdefault("redis.asyncio", _redis_asyncio_mock)

# Mock config 模块
_config_mock = ModuleType("core.config")
_settings = MagicMock()
_settings.rate_limit_global_tasks = 15
_settings.rate_limit_conversation_tasks = 5
_config_mock.settings = _settings
sys.modules.setdefault("core", ModuleType("core"))
sys.modules.setdefault("core.config", _config_mock)

# Mock exceptions
_exc_mock = ModuleType("core.exceptions")


class _TaskQueueFullError(Exception):
    def __init__(self, current_count, max_count, scope="global"):
        self.details = {
            "current_count": current_count,
            "max_count": max_count,
            "scope": scope,
        }
        super().__init__(f"TaskQueueFull: {scope}")


_exc_mock.TaskQueueFullError = _TaskQueueFullError
sys.modules.setdefault("core.exceptions", _exc_mock)


# ============ Fixtures ============


@pytest.fixture
def mock_redis():
    """Mock Redis 客户端（支持 pipeline context manager + SET 操作）"""
    redis = AsyncMock()
    redis.scard = AsyncMock(return_value=0)

    # pipeline 返回 async context manager
    pipe = AsyncMock()
    pipe.scard = AsyncMock()
    pipe.sadd = AsyncMock()
    pipe.srem = AsyncMock()
    pipe.expire = AsyncMock()
    pipe.execute = AsyncMock(return_value=[0, 0])

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=pipe)
    ctx.__aexit__ = AsyncMock(return_value=False)
    redis.pipeline = MagicMock(return_value=ctx)

    return redis, pipe


@pytest.fixture
def service(mock_redis):
    """创建 TaskLimitService 实例"""
    redis_client, _ = mock_redis
    from services.task_limit_service import TaskLimitService
    svc = TaskLimitService(redis_client)
    svc.global_limit = 15
    svc.conversation_limit = 5
    return svc


# ============ check_and_acquire 测试 ============


class TestCheckAndAcquire:
    """check_and_acquire: SCARD 检查 + SADD 获取"""

    @pytest.mark.asyncio
    async def test_success_returns_slot_id(self, service, mock_redis):
        """计数均在限制内时返回 slot_id（UUID 字符串）"""
        _, pipe = mock_redis
        pipe.execute.return_value = [0, 0]

        slot_id = await service.check_and_acquire("user1", "conv1")

        assert isinstance(slot_id, str)
        assert len(slot_id) == 36  # UUID 格式
        # 应调用 sadd 两次（全局 + 对话 SET）
        assert pipe.sadd.await_count == 2

    @pytest.mark.asyncio
    async def test_global_limit_exceeded(self, service, mock_redis):
        """全局任务数达到上限时抛出 TaskQueueFullError"""
        _, pipe = mock_redis
        pipe.execute.return_value = [15, 0]

        TaskQueueFullError = _TaskQueueFullError

        with pytest.raises(TaskQueueFullError) as exc_info:
            await service.check_and_acquire("user1", "conv1")

        assert exc_info.value.details["scope"] == "global"
        assert exc_info.value.details["current_count"] == 15

    @pytest.mark.asyncio
    async def test_conversation_limit_exceeded(self, service, mock_redis):
        """单对话任务数达到上限时抛出 TaskQueueFullError"""
        _, pipe = mock_redis
        pipe.execute.return_value = [3, 5]

        TaskQueueFullError = _TaskQueueFullError

        with pytest.raises(TaskQueueFullError) as exc_info:
            await service.check_and_acquire("user1", "conv1")

        assert exc_info.value.details["scope"] == "conversation"
        assert exc_info.value.details["current_count"] == 5

    @pytest.mark.asyncio
    async def test_redis_error_degrades_to_allow(self, service, mock_redis):
        """Redis 异常时降级允许执行，仍返回 slot_id"""
        redis_client, _ = mock_redis
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("Redis down"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        redis_client.pipeline = MagicMock(return_value=ctx)

        slot_id = await service.check_and_acquire("user1", "conv1")

        assert isinstance(slot_id, str)
        assert len(slot_id) == 36


# ============ release 测试 ============


class TestRelease:
    """release: SREM 释放指定 slot_id"""

    @pytest.mark.asyncio
    async def test_release_removes_slot_from_both_sets(self, service, mock_redis):
        """释放时从全局和对话 SET 中移除 slot_id"""
        _, pipe = mock_redis

        await service.release("user1", "conv1", slot_id="slot-abc-123")

        assert pipe.srem.await_count == 2
        # 验证 srem 调用参数包含 slot_id
        calls = pipe.srem.await_args_list
        for call in calls:
            assert call.args[1] == "slot-abc-123"

    @pytest.mark.asyncio
    async def test_release_without_slot_id_is_noop(self, service, mock_redis):
        """无 slot_id 时静默跳过"""
        _, pipe = mock_redis

        await service.release("user1", "conv1", slot_id=None)

        assert pipe.srem.await_count == 0

    @pytest.mark.asyncio
    async def test_release_idempotent(self, service, mock_redis):
        """重复释放同一 slot_id 不报错（SET 幂等性）"""
        _, pipe = mock_redis
        pipe.execute.return_value = [0, 0]  # srem 返回 0（已不存在）

        # 连续释放两次不应抛异常
        await service.release("user1", "conv1", slot_id="slot-abc")
        await service.release("user1", "conv1", slot_id="slot-abc")

    @pytest.mark.asyncio
    async def test_release_ignores_redis_error(self, service, mock_redis):
        """Redis 异常时静默忽略"""
        redis_client, _ = mock_redis
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("Redis down"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        redis_client.pipeline = MagicMock(return_value=ctx)

        await service.release("user1", "conv1", slot_id="slot-abc")


# ============ get_active_count / can_start_task 测试 ============


class TestGetActiveCount:
    """get_active_count 和 can_start_task"""

    @pytest.mark.asyncio
    async def test_get_active_count_with_conversation(self, service, mock_redis):
        """同时获取全局和对话计数"""
        redis_client, _ = mock_redis
        redis_client.scard = AsyncMock(side_effect=[3, 2])

        result = await service.get_active_count("user1", "conv1")

        assert result["global"] == 3
        assert result["conversation"] == 2
        assert result["global_limit"] == 15
        assert result["conversation_limit"] == 5

    @pytest.mark.asyncio
    async def test_can_start_task_true(self, service, mock_redis):
        """未超限时返回 True"""
        redis_client, _ = mock_redis
        redis_client.scard = AsyncMock(side_effect=[5, 2])

        result = await service.can_start_task("user1", "conv1")

        assert result is True

    @pytest.mark.asyncio
    async def test_can_start_task_false_when_at_limit(self, service, mock_redis):
        """达到限制时返回 False"""
        redis_client, _ = mock_redis
        redis_client.scard = AsyncMock(side_effect=[15, 2])

        result = await service.can_start_task("user1", "conv1")

        assert result is False


# ============ Key 生成测试 ============


class TestKeyGeneration:
    """Redis key 格式验证（SET 模式新 key 前缀）"""

    def test_global_key_format(self, service):
        assert service._global_key("user_abc") == "task:active:personal:user_abc"
        assert service._global_key("user_abc", org_id="org_1") == "task:active:org_1:user_abc"

    def test_conversation_key_format(self, service):
        assert service._conversation_key("user_abc", "conv_123") == \
            "task:conv_active:personal:user_abc:conv_123"
        assert service._conversation_key("user_abc", "conv_123", org_id="org_1") == \
            "task:conv_active:org_1:user_abc:conv_123"


# ============ extract_slot_id 测试 ============


class TestExtractSlotId:
    """extract_slot_id: 从 task request_params 提取 _task_slot_id"""

    def _extract(self, task):
        from services.task_limit_service import extract_slot_id
        return extract_slot_id(task)

    def test_dict_params(self):
        """request_params 为 dict 时正常提取"""
        task = {"request_params": {"_task_slot_id": "slot-abc"}}
        assert self._extract(task) == "slot-abc"

    def test_json_string_params(self):
        """request_params 为 JSON 字符串时正常解析"""
        import json
        task = {"request_params": json.dumps({"_task_slot_id": "slot-xyz"})}
        assert self._extract(task) == "slot-xyz"

    def test_no_slot_id(self):
        """request_params 中没有 _task_slot_id 返回 None"""
        task = {"request_params": {"model": "flux-pro"}}
        assert self._extract(task) is None

    def test_empty_params(self):
        """request_params 为空 dict"""
        task = {"request_params": {}}
        assert self._extract(task) is None

    def test_none_params(self):
        """request_params 为 None"""
        task = {"request_params": None}
        assert self._extract(task) is None

    def test_missing_params_key(self):
        """task 中没有 request_params 字段"""
        task = {"user_id": "u1"}
        assert self._extract(task) is None

    def test_invalid_json_string(self):
        """request_params 为非法 JSON 字符串"""
        task = {"request_params": "not-json{"}
        assert self._extract(task) is None

    def test_empty_string_params(self):
        """request_params 为空字符串"""
        task = {"request_params": ""}
        assert self._extract(task) is None


# ============ release_task_slot 集成测试 ============


class TestReleaseTaskSlot:
    """release_task_slot: 从 task 提取 slot_id 并调用 service.release"""

    def _mock_deps(self, mock_service):
        """创建 api.deps mock 模块"""
        deps_mock = ModuleType("api.deps")
        deps_mock.get_task_limit_service = AsyncMock(return_value=mock_service)
        return deps_mock

    @pytest.mark.asyncio
    async def test_calls_release_with_slot_id(self):
        """有 slot_id 时调用 service.release"""
        mock_service = AsyncMock()
        mock_service.release = AsyncMock()
        deps_mock = self._mock_deps(mock_service)

        task = {
            "user_id": "u1",
            "conversation_id": "conv1",
            "org_id": "org1",
            "request_params": {"_task_slot_id": "slot-123"},
        }

        with patch.dict(sys.modules, {"api.deps": deps_mock}):
            from services.task_limit_service import release_task_slot
            await release_task_slot(task)

        mock_service.release.assert_awaited_once_with(
            "u1", "conv1", org_id="org1", slot_id="slot-123",
        )

    @pytest.mark.asyncio
    async def test_noop_without_slot_id(self):
        """没有 slot_id 时不调用 service"""
        mock_service = AsyncMock()
        deps_mock = self._mock_deps(mock_service)

        task = {
            "user_id": "u1",
            "conversation_id": "conv1",
            "request_params": {"model": "flux"},
        }

        with patch.dict(sys.modules, {"api.deps": deps_mock}):
            from services.task_limit_service import release_task_slot
            await release_task_slot(task)

        # 无 slot_id → 不应调 release
        mock_service.release.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_silent_on_service_error(self):
        """service 异常时静默不抛"""
        mock_service = AsyncMock()
        mock_service.release.side_effect = ConnectionError("Redis down")
        deps_mock = self._mock_deps(mock_service)

        task = {
            "user_id": "u1",
            "conversation_id": "conv1",
            "request_params": {"_task_slot_id": "slot-err"},
        }

        with patch.dict(sys.modules, {"api.deps": deps_mock}):
            from services.task_limit_service import release_task_slot
            await release_task_slot(task)  # 不应抛异常
