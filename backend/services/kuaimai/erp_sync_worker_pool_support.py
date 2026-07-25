"""Queue, lock, and per-organization concurrency support for ERP workers."""

from __future__ import annotations

import asyncio
import time

from loguru import logger


class LockLostError(Exception):
    """The task lock was lost and the current Sync task must stop."""


class ErpSyncWorkerPoolSupport:
    """Stateful support methods mixed into ErpSyncWorkerPool."""

    async def _dequeue(self) -> tuple[str, float] | None:
        try:
            from core.redis import RedisClient

            return await RedisClient.dequeue_task(
                self.settings.erp_sync_queue_key,
            )
        except Exception:
            return None

    async def _requeue_task(self, task_id: str) -> None:
        try:
            from core.redis import RedisClient

            await RedisClient.enqueue_task(
                self.settings.erp_sync_queue_key,
                task_id,
                time.time() + 5,
            )
        except Exception:
            pass

    async def _acquire_task_lock(self, lock_key: str) -> str | None:
        try:
            from core.redis import RedisClient

            token = await RedisClient.acquire_lock(
                lock_key,
                timeout=self.settings.erp_sync_task_lock_ttl,
            )
            if token:
                self._held_locks[lock_key] = token
            return token
        except Exception as error:
            logger.warning(
                f"Task lock acquire failed | key={lock_key} error={error}"
            )
            return await self._acquire_task_lock_db(lock_key)

    async def _acquire_task_lock_db(self, lock_key: str) -> str | None:
        try:
            parts = lock_key.split(":")
            org_id_text = parts[1] if len(parts) >= 3 else None
            org_id = None if org_id_text == "__default__" else org_id_text
            result = await self.db.rpc(
                "erp_try_acquire_sync_lock",
                {
                    "p_lock_ttl_seconds": self.settings.erp_sync_task_lock_ttl,
                    "p_org_id": org_id,
                },
            ).execute()
            if bool(result.data):
                self._held_locks[lock_key] = "__db_lock__"
                return "__db_lock__"
            return None
        except Exception as error:
            logger.error(
                f"DB task lock failed | key={lock_key} error={error}"
            )
            return None

    def _make_extend_fn(
        self,
        lock_key: str,
        lock_lost_event: asyncio.Event,
    ):
        async def _extend() -> None:
            if lock_lost_event.is_set():
                raise LockLostError(
                    f"Lock lost (detected by renew loop) | key={lock_key}"
                )
            token = self._held_locks.get(lock_key)
            if not token or token == "__db_lock__":
                return
            try:
                from core.redis import RedisClient

                ok = await RedisClient.extend_lock(
                    lock_key,
                    token,
                    self.settings.erp_sync_task_lock_ttl,
                )
                if not ok:
                    self._held_locks.pop(lock_key, None)
                    lock_lost_event.set()
                    raise LockLostError(
                        f"Lock lost (token mismatch) | key={lock_key}"
                    )
            except LockLostError:
                raise
            except Exception as error:
                logger.warning(
                    f"Lock extend Redis error | key={lock_key} error={error}"
                )

        return _extend

    async def _release_task_lock(
        self,
        lock_key: str,
        token: str,
    ) -> None:
        self._held_locks.pop(lock_key, None)
        if token == "__db_lock__":
            return
        try:
            from core.redis import RedisClient

            await RedisClient.release_lock(lock_key, token)
        except Exception:
            pass

    async def _release_all_locks(self) -> None:
        for lock_key, token in list(self._held_locks.items()):
            await self._release_task_lock(lock_key, token)
        self._held_locks.clear()

    async def _check_org_concurrency(self, org_id: str | None) -> bool:
        try:
            from core.redis import RedisClient

            key = f"{self._concurrency_prefix}:{org_id or '__default__'}"
            count = await RedisClient.incr_with_ttl(key, ttl=600)
            if count > self.settings.erp_sync_max_org_concurrency:
                await RedisClient.decr_floor(key)
                return False
            return True
        except Exception:
            return True

    async def _decr_org_concurrency(self, org_id: str | None) -> None:
        try:
            from core.redis import RedisClient

            key = f"{self._concurrency_prefix}:{org_id or '__default__'}"
            await RedisClient.decr_floor(key)
        except Exception:
            pass
