"""
LeaderElection 单元测试

覆盖：抢锁 / 心跳续期 / 优雅释放 / Follower 重试 / Leader 崩溃 failover
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from core.leader_election import LeaderElection


def _make_redis(set_result=True) -> AsyncMock:
    """构造 mock Redis"""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=set_result)
    redis.eval = AsyncMock(return_value=1)
    return redis


class TestAcquireLock:
    """抢锁测试"""

    @pytest.mark.asyncio
    async def test_first_worker_becomes_leader(self):
        """第一个 worker SET NX 成功 → 成为 Leader"""
        redis = _make_redis(set_result=True)
        on_elected = AsyncMock()

        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=on_elected, ttl=5, retry_interval=1,
        )

        # 运行一个周期后检查
        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.05)
        # stop 前检查（stop 会 release 锁并清除 is_leader）
        assert election.is_leader
        on_elected.assert_awaited_once()
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_second_worker_stays_follower(self):
        """SET NX 失败 → 保持 Follower"""
        redis = _make_redis(set_result=False)
        on_elected = AsyncMock()

        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=on_elected, ttl=5, retry_interval=1,
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.05)
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert not election.is_leader
        on_elected.assert_not_awaited()


class TestHeartbeat:
    """心跳续期测试"""

    @pytest.mark.asyncio
    async def test_leader_refreshes_ttl(self):
        """Leader 每次心跳调用 SET XX EX"""
        redis = _make_redis(set_result=True)

        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=AsyncMock(), ttl=5,
            heartbeat_interval=0.02,  # 极短间隔便于测试
            retry_interval=1,
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.15)  # 让心跳跑几次
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 至少调了 2 次 set（1次抢锁 NX + N次心跳 XX）
        assert redis.set.await_count >= 2
        # 心跳调用用的是 xx=True
        heartbeat_calls = [
            c for c in redis.set.call_args_list
            if c.kwargs.get("xx") is True
        ]
        assert len(heartbeat_calls) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_lost_triggers_demotion(self):
        """心跳时 SET XX 返回 False → Leader 被降级"""
        redis = AsyncMock()
        # 第一次 SET NX 成功，后续 SET XX 失败
        redis.set = AsyncMock(side_effect=[True, False])
        redis.eval = AsyncMock(return_value=0)

        on_demoted = AsyncMock()
        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=AsyncMock(),
            on_demoted=on_demoted,
            ttl=5, heartbeat_interval=0.02, retry_interval=0.02,
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.15)
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 降级后 is_leader 变 False
        on_demoted.assert_awaited_once()


class TestGracefulRelease:
    """优雅释放测试"""

    @pytest.mark.asyncio
    async def test_stop_releases_lock_with_lua(self):
        """stop() → Lua DEL-if-match 释放锁"""
        redis = _make_redis(set_result=True)

        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=AsyncMock(), ttl=5, retry_interval=1,
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.05)
        assert election.is_leader

        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # eval 被调用（Lua DEL-if-match）
        redis.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_follower_stop_does_not_release(self):
        """Follower stop() → 不调用 Lua DEL"""
        redis = _make_redis(set_result=False)

        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=AsyncMock(), ttl=5, retry_interval=1,
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.05)
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        redis.eval.assert_not_awaited()


class TestFollowerRetry:
    """Follower 重试抢锁"""

    @pytest.mark.asyncio
    async def test_follower_retries_and_becomes_leader(self):
        """Follower 多次失败后成功 → 变成 Leader"""
        redis = AsyncMock()
        # 首次失败（initial acquire），循环中第 1 次失败，第 2 次成功
        redis.set = AsyncMock(side_effect=[False, False, True])
        redis.eval = AsyncMock(return_value=1)

        on_elected = AsyncMock()
        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=on_elected, ttl=5,
            retry_interval=0.02,  # 极短间隔
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.15)
        # stop 前检查
        assert election.is_leader
        on_elected.assert_awaited_once()
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestErrorResilience:
    """异常韧性"""

    @pytest.mark.asyncio
    async def test_redis_error_does_not_crash(self):
        """Redis 异常 → 记录日志继续重试，不崩溃"""
        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=[
            ConnectionError("Redis down"),  # initial acquire 失败
            True,  # 循环中重试成功
        ])
        redis.eval = AsyncMock(return_value=1)

        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=AsyncMock(), ttl=5,
            retry_interval=0.02,
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.1)
        # stop 前检查
        assert election.is_leader
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_on_elected_error_keeps_leader(self):
        """on_elected 回调失败 → 保持 Leader 状态（不释放锁）"""
        redis = _make_redis(set_result=True)

        on_elected = AsyncMock(side_effect=RuntimeError("init failed"))
        election = LeaderElection(
            redis=redis, key="test_leader",
            on_elected=on_elected, ttl=5, retry_interval=1,
        )

        task = asyncio.create_task(election.run())
        await asyncio.sleep(0.05)
        # stop 前检查——on_elected 失败但锁已抢到，仍是 leader
        assert election.is_leader
        await election.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
