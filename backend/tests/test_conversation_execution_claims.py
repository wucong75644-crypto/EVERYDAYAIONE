"""ConversationExecutionService 的 Worker claim RPC 测试。"""

from types import SimpleNamespace
from typing import Any

import pytest

from services.conversation_execution import (
    ConversationExecutionService,
    GenerationOutcome,
)


def _claimed(mode: str = "serial") -> dict[str, Any]:
    return {
        "outcome": "claimed",
        "task_id": "task-1",
        "execution_token": "token-1",
        "turn_id": "turn-1",
        "input_message_id": "input-1",
        "base_context_revision": 4,
        "context_through_message_id": "closed-1",
        "execution_attempt": 1,
        "execution_mode": mode,
        "user_id": "user-1",
        "org_id": "org-1",
    }


class _Caller:
    def __init__(self, value: dict[str, Any]) -> None:
        self._value = value

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._value)


class _DB:
    def __init__(self, value: dict[str, Any]) -> None:
        self._value = value
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _Caller:
        self.calls.append((name, params))
        return _Caller(self._value)


class _UnusedExecutor:
    async def execute(self, task, claim, cancellation_event) -> GenerationOutcome:
        raise AssertionError("claim tests must not execute tasks")


@pytest.mark.asyncio
async def test_claim_serial_returns_typed_claim() -> None:
    db = _DB(_claimed())
    service = ConversationExecutionService(db, _UnusedExecutor())

    claim = await service.claim_serial("conv-1")

    assert claim is not None
    assert claim.conversation_id == "conv-1"
    assert claim.base_context_revision == 4
    assert claim.execution_mode == "serial"


@pytest.mark.asyncio
async def test_claim_returns_none_when_queue_is_busy() -> None:
    service = ConversationExecutionService(
        _DB({"outcome": "busy"}),
        _UnusedExecutor(),
    )

    assert await service.claim_serial("conv-1") is None


@pytest.mark.asyncio
async def test_claim_branch_uses_exact_task_without_serial_owner() -> None:
    db = _DB(_claimed("branch"))
    service = ConversationExecutionService(db, _UnusedExecutor())

    claim = await service.claim_branch("task-1", "conv-1")

    assert claim is not None
    assert claim.execution_mode == "branch"
    assert db.calls[0] == (
        "worker_claim_branch_generation_turn",
        {
            "p_task_id": "task-1",
            "p_lease_seconds": 90,
            "p_max_attempts": 3,
        },
    )
