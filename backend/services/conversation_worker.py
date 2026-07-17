"""Conversation Actor 的持久队列扫描 Worker 与 Redis 唤醒适配器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from loguru import logger

from services.conversation_execution import ConversationExecutionService


_WAKEUP_PREFIX = "actor:wakeup"
_WAKEUP_PATTERN = f"{_WAKEUP_PREFIX}:*"


class WakeupBus(Protocol):
    async def publish(
        self,
        conversation_id: str,
        org_id: str | None,
    ) -> bool:
        """Best-effort 发布 conversation 唤醒。"""

    async def listen(
        self,
        handler: Callable[[str], Awaitable[None]],
    ) -> None:
        """持续监听 conversation 唤醒。"""

    async def close(self) -> None:
        """关闭监听资源。"""


class RedisConversationWakeup:
    """Redis 只传递唤醒，不保存队列、上下文或执行权。"""

    def __init__(self, redis_factory: Callable[[], Awaitable[Any]] | None = None) -> None:
        self._redis_factory = redis_factory
        self._pubsub: Any = None

    async def _get_redis(self) -> Any:
        if self._redis_factory:
            return await self._redis_factory()
        from core.redis import RedisClient
        return await RedisClient.get_client()

    async def publish(
        self,
        conversation_id: str,
        org_id: str | None,
    ) -> bool:
        channel = f"{_WAKEUP_PREFIX}:{org_id or 'personal'}:{conversation_id}"
        try:
            redis = await self._get_redis()
            await redis.publish(channel, conversation_id)
            return True
        except Exception as error:
            logger.warning(
                "actor_wakeup_publish_failed | "
                f"org_id={org_id} | conversation_id={conversation_id} | "
                f"error={type(error).__name__}"
            )
            return False

    async def listen(
        self,
        handler: Callable[[str], Awaitable[None]],
    ) -> None:
        retry_delay = 1.0
        while True:
            try:
                await self._listen_once(handler)
                retry_delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "actor_wakeup_listener_failed | "
                    f"retry_seconds={retry_delay} | error={type(error).__name__}"
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    async def _listen_once(
        self,
        handler: Callable[[str], Awaitable[None]],
    ) -> None:
        redis = await self._get_redis()
        self._pubsub = redis.pubsub()
        await self._pubsub.psubscribe(_WAKEUP_PATTERN)
        try:
            while True:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if not message:
                    await asyncio.sleep(0)
                    continue
                conversation_id = message.get("data")
                if isinstance(conversation_id, bytes):
                    conversation_id = conversation_id.decode("utf-8")
                if isinstance(conversation_id, str) and conversation_id:
                    await handler(conversation_id)
        except asyncio.CancelledError:
            raise
        finally:
            await self.close()

    async def close(self) -> None:
        if self._pubsub is None:
            return
        pubsub, self._pubsub = self._pubsub, None
        try:
            await pubsub.punsubscribe(_WAKEUP_PATTERN)
            await pubsub.aclose()
        except Exception as error:
            logger.debug(
                f"actor_wakeup_close_failed | error={type(error).__name__}"
            )


@dataclass(frozen=True)
class _Candidate:
    task_id: str
    conversation_id: str
    execution_mode: str

    @property
    def key(self) -> str:
        if self.execution_mode == "branch":
            return f"branch:{self.task_id}"
        return f"serial:{self.conversation_id}"


class ConversationWorker:
    """以数据库为事实源的有界 Conversation Actor Worker。"""

    def __init__(
        self,
        db: Any,
        execution: ConversationExecutionService,
        *,
        wakeup_bus: WakeupBus | None = None,
        scan_interval_seconds: float = 2,
        concurrency: int = 5,
        scan_batch_size: int = 100,
        shutdown_timeout_seconds: float = 10,
    ) -> None:
        if scan_interval_seconds <= 0:
            raise ValueError("scan_interval_seconds must be positive")
        if concurrency < 1 or scan_batch_size < 1:
            raise ValueError("concurrency and scan_batch_size must be positive")
        if shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        self._db = db
        self._execution = execution
        self._wakeup_bus = wakeup_bus
        self._scan_interval = scan_interval_seconds
        self._concurrency = concurrency
        self._scan_batch_size = scan_batch_size
        self._shutdown_timeout = shutdown_timeout_seconds
        self._running = False
        self._wake_event = asyncio.Event()
        self._woken_conversations: set[str] = set()
        self._active_keys: set[str] = set()
        self._execution_tasks: set[asyncio.Task[Any]] = set()
        self._listener_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._wakeup_bus:
            self._listener_task = asyncio.create_task(
                self._wakeup_bus.listen(self.wake)
            )
        logger.info(
            "ConversationWorker started | "
            f"concurrency={self._concurrency} | batch={self._scan_batch_size}"
        )
        try:
            while self._running:
                self._wake_event.clear()
                await self.scan_once()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(),
                        timeout=self._scan_interval,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._stop_listener()
            await self._drain_execution_tasks()
            logger.info("ConversationWorker stopped")

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._wakeup_bus:
            await self._wakeup_bus.close()
        await self._stop_listener()
        await self._drain_execution_tasks()

    async def wake(self, conversation_id: str) -> None:
        if conversation_id:
            self._woken_conversations.add(conversation_id)
            self._wake_event.set()

    async def scan_once(self) -> int:
        candidates = await self._load_candidates()
        for conversation_id in self._drain_wakeups():
            candidates.insert(0, _Candidate("", conversation_id, "serial"))

        available = self._concurrency - len(self._execution_tasks)
        scheduled = 0
        seen: set[str] = set()
        for candidate in candidates:
            if scheduled >= available:
                break
            if candidate.key in seen or candidate.key in self._active_keys:
                continue
            seen.add(candidate.key)
            self._schedule(candidate)
            scheduled += 1
        return scheduled

    async def wait_idle(self) -> None:
        while self._execution_tasks:
            await asyncio.gather(
                *tuple(self._execution_tasks),
                return_exceptions=True,
            )

    async def _load_candidates(self) -> list[_Candidate]:
        try:
            result = await (
                self._db.table("tasks")
                .select("id, conversation_id, execution_mode, delivery_context")
                .eq("type", "chat")
                .in_("status", ["pending", "running"])
                .order("queue_sequence")
                .limit(self._scan_batch_size)
                .execute()
            )
        except Exception as error:
            logger.warning(
                f"actor_scan_failed | error={type(error).__name__}"
            )
            return []

        candidates: list[_Candidate] = []
        for row in result.data or []:
            from services.conversation_task import is_actor_task
            if not is_actor_task(row):
                continue
            task_id = row.get("id")
            conversation_id = row.get("conversation_id")
            mode = row.get("execution_mode", "serial")
            if task_id and conversation_id and mode in {"serial", "branch"}:
                candidates.append(
                    _Candidate(str(task_id), str(conversation_id), mode)
                )
        return candidates

    def _drain_wakeups(self) -> list[str]:
        conversations = list(self._woken_conversations)
        self._woken_conversations.clear()
        return conversations

    def _schedule(self, candidate: _Candidate) -> None:
        self._active_keys.add(candidate.key)
        task = asyncio.create_task(self._run_candidate(candidate))
        self._execution_tasks.add(task)
        task.add_done_callback(self._execution_tasks.discard)

    async def _run_candidate(self, candidate: _Candidate) -> None:
        try:
            if candidate.execution_mode == "branch":
                claim = await self._execution.claim_branch(
                    candidate.task_id, candidate.conversation_id,
                )
            else:
                claim = await self._execution.claim_serial(
                    candidate.conversation_id,
                )
            if claim is not None:
                await self._execution.execute_claim(claim)
                await self.wake(candidate.conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "actor_candidate_failed | "
                f"conversation_id={candidate.conversation_id} | "
                f"task_id={candidate.task_id or 'serial'} | "
                f"error={type(error).__name__}"
            )
        finally:
            self._active_keys.discard(candidate.key)

    async def _stop_listener(self) -> None:
        if not self._listener_task:
            return
        listener, self._listener_task = self._listener_task, None
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)

    async def _drain_execution_tasks(self) -> None:
        if not self._execution_tasks:
            return
        tasks = tuple(self._execution_tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._shutdown_timeout,
            )
        except asyncio.TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
