"""ConversationExecutionService 的 Worker/任务 DB 路由测试。"""

from types import SimpleNamespace
from typing import Any

import pytest

from services.conversation_db_scope import ActorTaskDatabases
from services.conversation_execution import (
    ConversationExecutionService,
    GenerationClaim,
    GenerationOutcome,
)


class _Caller:
    def __init__(self, value: dict[str, Any]) -> None:
        self._value = value

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._value)


class _TaskQuery:
    def __init__(self, task: dict[str, Any]) -> None:
        self._task = task

    def select(self, _fields: str) -> "_TaskQuery":
        return self

    def eq(self, _field: str, _value: Any) -> "_TaskQuery":
        return self

    def maybe_single(self) -> "_TaskQuery":
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._task)


class _DB:
    def __init__(self, task: dict[str, Any] | None = None) -> None:
        self.task = task
        self.rpc_names: list[str] = []

    def table(self, name: str) -> _TaskQuery:
        assert name == "tasks"
        assert self.task is not None
        return _TaskQuery(self.task)

    def rpc(self, name: str, _params: dict[str, Any]) -> _Caller:
        self.rpc_names.append(name)
        return _Caller({"outcome": "committed"})


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task, claim, cancellation_event) -> GenerationOutcome:
        self.calls += 1
        return GenerationOutcome([], {}, 0)


class _Observer:
    def __init__(self) -> None:
        self.calls = 0

    async def notify(self, task, terminal_result) -> None:
        self.calls += 1


def _claim() -> GenerationClaim:
    return GenerationClaim(
        task_id="task-1",
        execution_token="token-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        input_message_id="input-1",
        base_context_revision=1,
        context_through_message_id=None,
        execution_attempt=1,
        execution_mode="serial",
    )


@pytest.mark.asyncio
async def test_claim_execution_switches_from_worker_to_task_databases() -> None:
    task = {
        "id": "task-1",
        "conversation_id": "conv-1",
        "assistant_message_id": "output-1",
    }
    worker_db = _DB(task)
    control_db = _DB()
    application_db = object()
    executor = _Executor()
    observer = _Observer()
    seen: list[ActorTaskDatabases] = []
    databases = ActorTaskDatabases(control_db, application_db, object())

    service = ConversationExecutionService(
        worker_db,
        _Executor(),
        task_db_factory=lambda _loaded: databases,
        executor_factory=lambda scoped: seen.append(scoped) or executor,
        terminal_observer_factory=lambda scoped: (
            observer if scoped is databases else None
        ),
    )

    result = await service.execute_claim(_claim())

    assert result == {"outcome": "committed"}
    assert seen == [databases]
    assert executor.calls == 1
    assert observer.calls == 1
    assert worker_db.rpc_names == []
    assert control_db.rpc_names == [
        "commit_generation_turn_with_context_v2",
    ]
