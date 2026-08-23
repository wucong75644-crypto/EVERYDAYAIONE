"""Conversation Actor 崩溃、租约和多 Worker 互斥回归矩阵。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from services.conversation_execution import GenerationClaim
from services.conversation_worker import ConversationWorker


def _row() -> dict[str, Any]:
    return {
        "id": "task-1",
        "conversation_id": "conversation-1",
        "execution_mode": "serial",
        "delivery_context": {"actor": True},
    }


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.limit_value = 100

    def select(self, _fields: str) -> "_Query":
        return self

    def eq(self, _field: str, _value: Any) -> "_Query":
        return self

    def in_(self, _field: str, _values: list[str]) -> "_Query":
        return self

    def order(self, _field: str) -> "_Query":
        return self

    def limit(self, value: int) -> "_Query":
        self.limit_value = value
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.rows[: self.limit_value])


class _DB:
    def table(self, name: str) -> _Query:
        assert name == "tasks"
        return _Query([_row()])


class _SharedClaim:
    """模拟两个 Worker 共享同一个数据库 claim RPC。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.claimed = False
        self.executed = 0

    async def claim_serial(self, conversation_id: str) -> GenerationClaim | None:
        async with self._lock:
            if self.claimed:
                return None
            self.claimed = True
            return GenerationClaim(
                task_id="task-1",
                execution_token="token-1",
                conversation_id=conversation_id,
                turn_id="turn-1",
                input_message_id="input-1",
                base_context_revision=0,
                context_through_message_id=None,
                execution_attempt=1,
                execution_mode="serial",
            )

    async def claim_branch(self, task_id: str, conversation_id: str):
        raise AssertionError("serial scenario must not claim a branch")

    async def execute_claim(self, _claim: GenerationClaim) -> dict[str, str]:
        self.executed += 1
        return {"outcome": "committed"}


@pytest.mark.asyncio
async def test_two_workers_same_serial_conversation_execute_once() -> None:
    execution = _SharedClaim()
    worker_a = ConversationWorker(_DB(), execution)
    worker_b = ConversationWorker(_DB(), execution)

    await asyncio.gather(worker_a.scan_once(), worker_b.scan_once())
    await asyncio.gather(worker_a.wait_idle(), worker_b.wait_idle())

    assert execution.executed == 1
