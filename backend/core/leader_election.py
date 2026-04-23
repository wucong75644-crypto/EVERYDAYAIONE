"""Leader Election — Redis SETNX + TTL + 心跳续期

对齐 Celery Beat / K8s Lease / Sidekiq Enterprise 的工业标准模式：
- Leader:   SET key worker_id NX EX ttl  （抢锁）
            每 ttl/3: SET key worker_id XX EX ttl  （心跳续期）
            shutdown: Lua DEL-if-match  （只删自己的锁）
- Follower: 每 retry_interval: SET key worker_id NX EX ttl  （抢锁重试）
            一旦成功 → 变成 Leader，执行 on_elected 回调

参数对齐 K8s LeaderElection: ttl=30s, heartbeat=10s, retry=10s
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Optional

from loguru import logger


# Lua 脚本：只删除自己持有的锁（防止误删别人的锁）
_LUA_DEL_IF_MATCH = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# 默认参数（对齐 K8s Lease 默认值）
_DEFAULT_TTL = 30           # 锁 TTL 秒数
_DEFAULT_HEARTBEAT = 10     # 心跳间隔 = TTL / 3
_DEFAULT_RETRY = 10         # Follower 重试间隔


class LeaderElection:
    """Redis-based leader election with heartbeat renewal.

    Usage:
        election = LeaderElection(
            redis=redis_client,
            key="erp_sync_leader",
            on_elected=start_sync_orchestrator,
            on_demoted=stop_sync_orchestrator,
        )
        task = asyncio.create_task(election.run())
        # ... on shutdown:
        await election.stop()
    """

    def __init__(
        self,
        redis: Any,
        key: str,
        on_elected: Callable[[], Awaitable[None]],
        on_demoted: Optional[Callable[[], Awaitable[None]]] = None,
        ttl: int = _DEFAULT_TTL,
        heartbeat_interval: int = _DEFAULT_HEARTBEAT,
        retry_interval: int = _DEFAULT_RETRY,
    ) -> None:
        self._redis = redis
        self._key = key
        self._on_elected = on_elected
        self._on_demoted = on_demoted
        self._ttl = ttl
        self._heartbeat_interval = heartbeat_interval
        self._retry_interval = retry_interval
        self._worker_id = f"{os.getpid()}"
        self._is_leader = False
        self._running = False

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    async def run(self) -> None:
        """主循环：Leader 心跳续期 / Follower 定期抢锁"""
        self._running = True
        logger.info(
            f"LeaderElection started | key={self._key} | "
            f"worker={self._worker_id} | ttl={self._ttl}s"
        )

        # 首次立即尝试抢锁（不等 retry_interval）
        try:
            await self._try_acquire()
        except asyncio.CancelledError:
            await self._release()
            return
        except Exception as e:
            logger.warning(
                f"LeaderElection initial acquire error | key={self._key} | error={e}"
            )

        while self._running:
            try:
                if self._is_leader:
                    await asyncio.sleep(self._heartbeat_interval)
                    await self._heartbeat()
                else:
                    await asyncio.sleep(self._retry_interval)
                    await self._try_acquire()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    f"LeaderElection error | key={self._key} | "
                    f"worker={self._worker_id} | error={e}"
                )
                await asyncio.sleep(self._retry_interval)

        # 退出前释放锁
        await self._release()

    async def stop(self) -> None:
        """优雅停止：释放锁 + 退出循环"""
        self._running = False

    async def _try_acquire(self) -> None:
        """Follower 尝试抢锁"""
        acquired = await self._redis.set(
            self._key, self._worker_id,
            nx=True, ex=self._ttl,
        )
        if acquired:
            self._is_leader = True
            logger.info(
                f"LeaderElection acquired | key={self._key} | "
                f"worker={self._worker_id}"
            )
            try:
                await self._on_elected()
            except Exception as e:
                logger.opt(exception=True).error(
                    f"LeaderElection on_elected failed | error={e}"
                )
                # on_elected 失败不释放锁，下次心跳时会重试或过期

    async def _heartbeat(self) -> None:
        """Leader 心跳续期：SET XX EX（只在 key 存在时刷新 TTL）"""
        refreshed = await self._redis.set(
            self._key, self._worker_id,
            xx=True, ex=self._ttl,
        )
        if not refreshed:
            # 锁被别人抢了（不应该发生，但防御性处理）
            logger.warning(
                f"LeaderElection lost lock | key={self._key} | "
                f"worker={self._worker_id}"
            )
            self._is_leader = False
            if self._on_demoted:
                try:
                    await self._on_demoted()
                except Exception as e:
                    logger.error(f"LeaderElection on_demoted failed | error={e}")

    async def _release(self) -> None:
        """优雅释放：Lua DEL-if-match（只删自己的锁）"""
        if not self._is_leader:
            return
        try:
            result = await self._redis.eval(
                _LUA_DEL_IF_MATCH, 1, self._key, self._worker_id,
            )
            self._is_leader = False
            logger.info(
                f"LeaderElection released | key={self._key} | "
                f"worker={self._worker_id} | result={result}"
            )
        except Exception as e:
            logger.warning(
                f"LeaderElection release failed | key={self._key} | "
                f"worker={self._worker_id} | error={e}"
            )
