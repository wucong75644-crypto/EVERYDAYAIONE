"""ConversationExecutionService 单元测试。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from psycopg.errors import CheckViolation

from services.conversation_execution import (
    ConversationExecutionService,
    GenerationClaim,
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
    }


class _AsyncCaller:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def execute(self) -> SimpleNamespace:
        if isinstance(self._value, Exception):
            raise self._value
        return SimpleNamespace(data=self._value)


class _TaskQuery:
    def __init__(self, task: dict[str, Any] | None) -> None:
        self._task = task

    def select(self, _fields: str) -> "_TaskQuery":
        return self

    def eq(self, _field: str, _value: Any) -> "_TaskQuery":
        return self

    def maybe_single(self) -> "_TaskQuery":
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._task)


class _FakeDB:
    def __init__(self) -> None:
        self.task = {
            "id": "task-1",
            "conversation_id": "conv-1",
            "assistant_message_id": "output-1",
        }
        self.results: dict[str, list[Any]] = defaultdict(list)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def queue(self, name: str, *values: Any) -> None:
        self.results[name].extend(values)

    def rpc(self, name: str, params: dict[str, Any]) -> _AsyncCaller:
        self.calls.append((name, params))
        values = self.results[name]
        value = values.pop(0) if values else {"outcome": "renewed"}
        return _AsyncCaller(value)

    def table(self, name: str) -> _TaskQuery:
        assert name == "tasks"
        return _TaskQuery(self.task)


class _SuccessExecutor:
    async def execute(self, task, claim, cancellation_event) -> GenerationOutcome:
        assert task["id"] == claim.task_id
        assert cancellation_event.is_set() is False
        return GenerationOutcome(
            result_content=[{"type": "text", "text": "ok"}],
            usage={"prompt_tokens": 2, "completion_tokens": 1},
            credits_cost=3,
            tool_digest=None,
        )


class _FailingExecutor:
    async def execute(self, task, claim, cancellation_event) -> GenerationOutcome:
        raise ValueError("provider failed")


class _BlockingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, task, claim, cancellation_event) -> GenerationOutcome:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _InvalidExecutor:
    async def execute(self, task, claim, cancellation_event):
        return {"content": "invalid"}


class _Observer:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = []
        self.error = error

    async def notify(self, task, terminal_result) -> None:
        self.calls.append((task, terminal_result))
        if self.error:
            raise self.error


@pytest.mark.asyncio
async def test_claim_serial_returns_typed_claim() -> None:
    db = _FakeDB()
    db.queue("claim_next_serial_generation_turn", _claimed())
    service = ConversationExecutionService(db, _SuccessExecutor())

    claim = await service.claim_serial("conv-1")

    assert claim is not None
    assert claim.conversation_id == "conv-1"
    assert claim.base_context_revision == 4
    assert claim.execution_mode == "serial"


@pytest.mark.asyncio
async def test_claim_returns_none_when_queue_is_busy() -> None:
    db = _FakeDB()
    db.queue("claim_next_serial_generation_turn", {"outcome": "busy"})
    service = ConversationExecutionService(db, _SuccessExecutor())

    assert await service.claim_serial("conv-1") is None


@pytest.mark.asyncio
async def test_claim_branch_uses_exact_task_without_serial_owner() -> None:
    db = _FakeDB()
    db.queue("claim_branch_generation_turn", _claimed("branch"))
    service = ConversationExecutionService(db, _SuccessExecutor())

    claim = await service.claim_branch("task-1", "conv-1")

    assert claim is not None
    assert claim.execution_mode == "branch"
    assert db.calls[0] == (
        "claim_branch_generation_turn",
        {
            "p_task_id": "task-1",
            "p_lease_seconds": 90,
            "p_max_attempts": 3,
        },
    )


@pytest.mark.asyncio
async def test_execute_claim_commits_executor_outcome() -> None:
    db = _FakeDB()
    db.queue("commit_generation_turn_with_context_v2", {"outcome": "committed"})
    service = ConversationExecutionService(db, _SuccessExecutor())
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    result = await service.execute_claim(claim)

    assert result == {"outcome": "committed"}
    commit = next(
        call for call in db.calls
        if call[0] == "commit_generation_turn_with_context_v2"
    )
    assert commit[1]["p_output_message_id"] == "output-1"
    assert commit[1]["p_credits_cost"] == 3
    assert commit[1]["p_result_content"].obj == [
        {"type": "text", "text": "ok"}
    ]
    assert commit[1]["p_usage"].obj == {
        "prompt_tokens": 2,
        "completion_tokens": 1,
    }
    assert commit[1]["p_data_evidence"].obj == []
    assert commit[1]["p_context_items"].obj == []
    assert commit[1]["p_artifacts"].obj == []
    assert commit[1]["p_context_receipts"].obj == []
    assert commit[1]["p_compaction"] is None


@pytest.mark.asyncio
async def test_non_committed_result_cleans_new_oss_artifacts() -> None:
    class _ArtifactExecutor:
        async def execute(self, task, claim, cancellation_event):
            return GenerationOutcome(
                result_content=[],
                usage={},
                credits_cost=0,
                artifacts=[{
                    "storage_kind": "oss",
                    "storage_ref": {"object_key": "artifact/new.json"},
                }],
            )

    db = _FakeDB()
    db.queue(
        "commit_generation_turn_with_context_v2",
        {"outcome": "ownership_lost"},
    )
    service = ConversationExecutionService(db, _ArtifactExecutor())

    with patch(
        "services.agent.runtime.artifacts.cleanup_materialized_artifacts",
        new=AsyncMock(),
    ) as cleanup:
        result = await service.execute_claim(
            GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")
        )

    assert result == {"outcome": "ownership_lost"}
    cleanup.assert_awaited_once_with(
        [{
            "storage_kind": "oss",
            "storage_ref": {"object_key": "artifact/new.json"},
        }],
        task_id="task-1",
    )


@pytest.mark.asyncio
async def test_execute_claim_commits_data_evidence_in_same_rpc() -> None:
    class _EvidenceExecutor:
        async def execute(self, task, claim, cancellation_event):
            return GenerationOutcome(
                result_content=[{"type": "text", "text": "1056"}],
                usage={},
                credits_cost=0,
                data_evidence=[
                    {
                        "artifact_id": "artifact-1",
                        "source": "runtime_validator",
                        "columns": [],
                        "rows": [{"valid_orders": 1056}],
                        "file_ref": None,
                        "query_scope": {},
                        "metric_definitions": {},
                        "lineage": {},
                        "validation_status": "ready",
                    }
                ],
            )

    db = _FakeDB()
    db.queue("commit_generation_turn_with_context_v2", {"outcome": "committed"})
    service = ConversationExecutionService(db, _EvidenceExecutor())

    await service.execute_claim(
        GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")
    )

    commit = next(
        call for call in db.calls
        if call[0] == "commit_generation_turn_with_context_v2"
    )
    evidence = commit[1]["p_data_evidence"].obj
    assert evidence[0]["artifact_id"] == "artifact-1"
    assert evidence[0]["rows"] == [{"valid_orders": 1056}]


@pytest.mark.asyncio
async def test_execute_claim_notifies_after_confirmed_commit() -> None:
    db = _FakeDB()
    db.queue("commit_generation_turn_with_context_v2", {"outcome": "committed"})
    observer = _Observer()
    service = ConversationExecutionService(
        db,
        _SuccessExecutor(),
        terminal_observer=observer,
    )

    result = await service.execute_claim(
        GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")
    )

    assert result == {"outcome": "committed"}
    assert observer.calls[0][1] == result


@pytest.mark.asyncio
async def test_terminal_observer_failure_does_not_change_database_outcome() -> None:
    db = _FakeDB()
    db.queue("commit_generation_turn_with_context_v2", {"outcome": "committed"})
    service = ConversationExecutionService(
        db,
        _SuccessExecutor(),
        terminal_observer=_Observer(RuntimeError("websocket down")),
    )

    result = await service.execute_claim(
        GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")
    )

    assert result == {"outcome": "committed"}


@pytest.mark.asyncio
async def test_executor_failure_uses_atomic_fail_rpc() -> None:
    db = _FakeDB()
    db.queue("fail_generation_turn", {"outcome": "failed"})
    service = ConversationExecutionService(db, _FailingExecutor())
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    result = await service.execute_claim(claim)

    assert result == {"outcome": "failed"}
    failure = next(call for call in db.calls if call[0] == "fail_generation_turn")
    assert failure[1]["p_error_code"] == "VALUEERROR"
    assert not any(
        name == "commit_generation_turn_with_context_v2"
        for name, _ in db.calls
    )


@pytest.mark.asyncio
async def test_invalid_executor_result_uses_atomic_fail_rpc() -> None:
    db = _FakeDB()
    db.queue("fail_generation_turn", {"outcome": "failed"})
    service = ConversationExecutionService(db, _InvalidExecutor())
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    result = await service.execute_claim(claim)

    assert result == {"outcome": "failed"}
    failure = next(call for call in db.calls if call[0] == "fail_generation_turn")
    assert failure[1]["p_error_code"] == "TYPEERROR"


@pytest.mark.asyncio
async def test_ownership_loss_cancels_local_executor() -> None:
    db = _FakeDB()
    db.queue("renew_generation_lease", {"outcome": "ownership_lost"})
    executor = _BlockingExecutor()
    service = ConversationExecutionService(
        db, executor, renew_interval_seconds=0.001,
    )
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    result = await service.execute_claim(claim)

    assert result == {"outcome": "ownership_lost"}
    assert executor.cancelled is True
    assert not any(
        name in {
            "commit_generation_turn_with_context_v2",
            "fail_generation_turn",
        }
        for name, _ in db.calls
    )


@pytest.mark.asyncio
async def test_consecutive_renew_errors_cancel_execution() -> None:
    db = _FakeDB()
    db.queue(
        "renew_generation_lease",
        ConnectionError("db down"),
        ConnectionError("db down"),
    )
    executor = _BlockingExecutor()
    service = ConversationExecutionService(
        db,
        executor,
        renew_interval_seconds=0.001,
        max_renew_failures=2,
    )
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    result = await service.execute_claim(claim)

    assert result == {"outcome": "ownership_lost"}
    assert executor.cancelled is True


@pytest.mark.asyncio
async def test_external_shutdown_does_not_write_false_terminal() -> None:
    db = _FakeDB()
    executor = _BlockingExecutor()
    service = ConversationExecutionService(db, executor)
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")
    running = asyncio.create_task(service.execute_claim(claim))
    await executor.started.wait()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert not any(
        name in {
            "commit_generation_turn_with_context_v2",
            "fail_generation_turn",
        }
        for name, _ in db.calls
    )


@pytest.mark.asyncio
async def test_cancel_winning_commit_race_is_returned_as_terminal() -> None:
    db = _FakeDB()
    db.queue(
        "commit_generation_turn_with_context_v2",
        {"outcome": "terminal", "status": "cancelled"},
    )
    service = ConversationExecutionService(db, _SuccessExecutor())
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    result = await service.execute_claim(claim)

    assert result == {"outcome": "terminal", "status": "cancelled"}
    assert not any(name == "fail_generation_turn" for name, _ in db.calls)


@pytest.mark.asyncio
async def test_commit_connection_error_does_not_write_false_failure() -> None:
    db = _FakeDB()
    db.queue(
        "commit_generation_turn_with_context_v2",
        ConnectionError("response lost"),
    )
    service = ConversationExecutionService(db, _SuccessExecutor())
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    with pytest.raises(ConnectionError, match="response lost"):
        await service.execute_claim(claim)

    assert not any(name == "fail_generation_turn" for name, _ in db.calls)


@pytest.mark.asyncio
async def test_commit_integrity_error_writes_confirmed_failure() -> None:
    db = _FakeDB()
    db.queue(
        "commit_generation_turn_with_context_v2",
        CheckViolation("artifact storage contract violated"),
    )
    db.queue("fail_generation_turn", {"outcome": "failed"})
    observer = _Observer()
    service = ConversationExecutionService(
        db,
        _SuccessExecutor(),
        terminal_observer=observer,
    )
    claim = GenerationClaim.from_rpc(_claimed(), "conv-1", "serial")

    result = await service.execute_claim(claim)

    assert result == {"outcome": "failed"}
    assert [name for name, _ in db.calls].count(
        "commit_generation_turn_with_context_v2"
    ) == 1
    assert [name for name, _ in db.calls].count("fail_generation_turn") == 1
    assert observer.calls[0][1] == result


def test_invalid_claim_result_is_rejected() -> None:
    data = _claimed()
    data["execution_token"] = None

    with pytest.raises(RuntimeError, match="ACTOR_CLAIM_RESULT_INVALID"):
        GenerationClaim.from_rpc(data, "conv-1", "serial")


def test_generation_outcome_rejects_negative_cost() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        GenerationOutcome([], {}, -1)
