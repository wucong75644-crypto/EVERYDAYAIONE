"""ConversationWorker 与 Redis 唤醒适配器单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from services.conversation_execution import GenerationClaim
from services.conversation_worker import (
    ConversationWorker,
    RedisConversationWakeup,
)


def _row(task: str, conversation: str, mode: str = "serial") -> dict[str, Any]:
    return {
        "id": task,
        "conversation_id": conversation,
        "execution_mode": mode,
        "delivery_context": {"actor": True},
    }


def _claim(task: str, conversation: str, mode: str) -> GenerationClaim:
    return GenerationClaim(
        task_id=task,
        execution_token=f"token-{task}",
        conversation_id=conversation,
        turn_id=f"turn-{task}",
        input_message_id=f"input-{task}",
        base_context_revision=0,
        context_through_message_id=None,
        execution_attempt=1,
        execution_mode=mode,
    )


class _Query:
    def __init__(self, db: "_DB") -> None:
        self._db = db

    def select(self, _fields: str) -> "_Query":
        return self

    def eq(self, _field: str, _value: Any) -> "_Query":
        return self

    def in_(self, _field: str, _values: list[str]) -> "_Query":
        return self

    def order(self, _field: str) -> "_Query":
        return self

    def limit(self, value: int) -> "_Query":
        self._limit = value
        return self

    async def execute(self) -> SimpleNamespace:
        if self._db.error:
            raise self._db.error
        return SimpleNamespace(data=self._db.rows[: self._limit])


class _DB:
    def __init__(self, rows: list[dict[str, str]] | None = None) -> None:
        self.rows = rows or []
        self.error: Exception | None = None

    def table(self, name: str) -> _Query:
        assert name == "tasks"
        return _Query(self)


class _WakeDuringScanQuery(_Query):
    async def execute(self) -> SimpleNamespace:
        await self._db.on_scan()
        return await super().execute()


class _WakeDuringScanDB(_DB):
    def __init__(self) -> None:
        super().__init__()
        self.on_scan = None

    def table(self, name: str) -> _WakeDuringScanQuery:
        assert name == "tasks"
        return _WakeDuringScanQuery(self)


class _Execution:
    def __init__(self) -> None:
        self.serial_calls: list[str] = []
        self.branch_calls: list[tuple[str, str]] = []
        self.executed: list[str] = []
        self.block = False
        self.started = asyncio.Event()
        self.cancelled = False

    async def claim_serial(self, conversation_id: str) -> GenerationClaim:
        self.serial_calls.append(conversation_id)
        return _claim(f"serial-{conversation_id}", conversation_id, "serial")

    async def claim_branch(
        self,
        task_id: str,
        conversation_id: str,
    ) -> GenerationClaim:
        self.branch_calls.append((task_id, conversation_id))
        return _claim(task_id, conversation_id, "branch")

    async def execute_claim(self, claim: GenerationClaim) -> dict[str, str]:
        self.executed.append(claim.task_id)
        self.started.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return {"outcome": "committed"}


class _Bus:
    def __init__(self) -> None:
        self.handler = None
        self.closed = False

    async def publish(self, conversation_id: str, org_id: str | None) -> bool:
        return True

    async def listen(self, handler) -> None:
        self.handler = handler
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_serial_candidates_are_deduplicated_by_conversation() -> None:
    db = _DB([_row("t1", "c1"), _row("t2", "c1")])
    execution = _Execution()
    worker = ConversationWorker(db, execution)

    assert await worker.scan_once() == 1
    await worker.wait_idle()

    assert execution.serial_calls == ["c1"]
    assert len(execution.executed) == 1


@pytest.mark.asyncio
async def test_branch_is_claimed_by_exact_task() -> None:
    db = _DB([_row("t1", "c1", "branch")])
    execution = _Execution()
    worker = ConversationWorker(db, execution)

    await worker.scan_once()
    await worker.wait_idle()

    assert execution.branch_calls == [("t1", "c1")]
    assert execution.executed == ["t1"]


@pytest.mark.asyncio
async def test_scan_respects_local_concurrency_limit() -> None:
    db = _DB([_row("t1", "c1"), _row("t2", "c2"), _row("t3", "c3")])
    execution = _Execution()
    execution.block = True
    worker = ConversationWorker(
        db, execution, concurrency=2, shutdown_timeout_seconds=0.01,
    )

    assert await worker.scan_once() == 2
    await execution.started.wait()
    assert len(execution.serial_calls) == 2
    await worker.stop()

    assert execution.cancelled is True


@pytest.mark.asyncio
async def test_database_scan_failure_is_non_fatal() -> None:
    db = _DB()
    db.error = ConnectionError("db unavailable")
    worker = ConversationWorker(db, _Execution())

    assert await worker.scan_once() == 0


@pytest.mark.asyncio
async def test_scan_ignores_legacy_chat_tasks() -> None:
    legacy = _row("legacy", "c1")
    legacy.pop("delivery_context")
    worker = ConversationWorker(_DB([legacy]), _Execution())

    assert await worker.scan_once() == 0


@pytest.mark.asyncio
async def test_wakeup_claims_conversation_without_scan_row() -> None:
    execution = _Execution()
    worker = ConversationWorker(_DB(), execution)
    await worker.wake("c-woken")

    assert await worker.scan_once() == 1
    await worker.wait_idle()

    assert execution.serial_calls == ["c-woken"]


@pytest.mark.asyncio
async def test_start_listens_and_stop_closes_bus() -> None:
    bus = _Bus()
    worker = ConversationWorker(
        _DB(),
        _Execution(),
        wakeup_bus=bus,
        scan_interval_seconds=60,
    )
    running = asyncio.create_task(worker.start())
    for _ in range(20):
        if bus.handler:
            break
        await asyncio.sleep(0)

    await bus.handler("c1")
    await asyncio.sleep(0)
    await worker.stop()
    await running

    assert bus.closed is True


@pytest.mark.asyncio
async def test_wakeup_during_scan_triggers_next_scan_without_poll_delay() -> None:
    db = _WakeDuringScanDB()
    execution = _Execution()
    worker = ConversationWorker(
        db,
        execution,
        scan_interval_seconds=60,
    )
    scan_count = 0

    async def wake_on_first_scan() -> None:
        nonlocal scan_count
        scan_count += 1
        if scan_count == 1:
            await worker.wake("c-race")

    db.on_scan = wake_on_first_scan
    running = asyncio.create_task(worker.start())
    await asyncio.wait_for(execution.started.wait(), timeout=0.2)
    await worker.stop()
    await running

    assert scan_count >= 2
    assert execution.serial_calls == ["c-race"]


class _Redis:
    def __init__(
        self,
        error: Exception | None = None,
        pubsub: Any = None,
    ) -> None:
        self.error = error
        self._pubsub = pubsub
        self.published: tuple[str, str] | None = None

    async def publish(self, channel: str, payload: str) -> None:
        if self.error:
            raise self.error
        self.published = (channel, payload)

    def pubsub(self):
        return self._pubsub


class _PubSub:
    def __init__(self) -> None:
        self.messages = [{"data": b"conv-1"}]
        self.closed = False

    async def psubscribe(self, _pattern: str) -> None:
        return None

    async def get_message(self, **_kwargs):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Event().wait()

    async def punsubscribe(self, _pattern: str) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_redis_publish_contains_only_routing_identifiers() -> None:
    redis = _Redis()

    async def factory():
        return redis

    bus = RedisConversationWakeup(factory)

    assert await bus.publish("conv-1", "org-1") is True
    assert redis.published == (
        "actor:wakeup:org-1:conv-1",
        "conv-1",
    )


@pytest.mark.asyncio
async def test_redis_publish_failure_degrades_to_database_scan() -> None:
    async def factory():
        return _Redis(ConnectionError("redis down"))

    bus = RedisConversationWakeup(factory)

    assert await bus.publish("conv-1", None) is False


@pytest.mark.asyncio
async def test_redis_listener_decodes_routing_identifier_and_closes() -> None:
    pubsub = _PubSub()

    async def factory():
        return _Redis(pubsub=pubsub)

    received: list[str] = []
    bus = RedisConversationWakeup(factory)

    async def handle_wakeup(conversation_id: str) -> None:
        received.append(conversation_id)

    listening = asyncio.create_task(bus.listen(handle_wakeup))
    for _ in range(20):
        if received:
            break
        await asyncio.sleep(0)

    listening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await listening

    assert received == ["conv-1"]
    assert pubsub.closed is True
