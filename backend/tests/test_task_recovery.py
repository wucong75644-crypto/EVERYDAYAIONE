"""Orphan task recovery RPC orchestration tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services import task_recovery


class _Response:
    def __init__(self, data: Any):
        self.data = data


class _Rpc:
    def __init__(self, db: "_RecoveryDb", name: str, params: dict[str, Any]):
        self._db = db
        self._name = name
        self._params = params

    def execute(self) -> _Response:
        self._db.calls.append((self._name, self._params))
        result = self._db.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return _Response(result)


class _RecoveryDb:
    def __init__(self, results: list[Any]):
        self.results = results
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _Rpc:
        return _Rpc(self, name, params)


def _task(**overrides: Any) -> dict[str, Any]:
    task = {
        "id": "11111111-1111-1111-1111-111111111111",
        "execution_token": "22222222-2222-2222-2222-222222222222",
        "type": "chat",
        "external_task_id": "external-1",
        "placeholder_message_id": "33333333-3333-3333-3333-333333333333",
        "conversation_id": "44444444-4444-4444-4444-444444444444",
        "model_id": "model-1",
        "client_task_id": "client-1",
        "accumulated_content": "部分内容",
        "accumulated_blocks": [],
    }
    task.update(overrides)
    return task


@pytest.fixture
def recovery_db(monkeypatch: pytest.MonkeyPatch) -> _RecoveryDb:
    db = _RecoveryDb([])
    monkeypatch.setattr(task_recovery, "_recovery_db", lambda _db: db)
    return db


@pytest.mark.asyncio
async def test_recover_content_claims_then_completes(
    recovery_db: _RecoveryDb,
) -> None:
    recovery_db.results.extend(
        [[_task()], {"outcome": "completed"}, []]
    )

    recovered = await task_recovery.recover_orphan_tasks(object())

    assert recovered == 1
    assert [call[0] for call in recovery_db.calls] == [
        "worker_claim_orphan_tasks",
        "worker_complete_orphan_task",
        "worker_claim_orphan_tasks",
    ]
    assert recovery_db.calls[1][1]["p_content"] == [
        {"type": "text", "text": "部分内容"}
    ]


@pytest.mark.asyncio
async def test_structured_blocks_are_merged_before_completion(
    recovery_db: _RecoveryDb,
) -> None:
    recovery_db.results.extend([
        [_task(
            accumulated_content="分析中最终回答",
            accumulated_blocks=[
                {"type": "text", "text": "分析中"},
                {
                    "type": "tool_step",
                    "tool_name": "query",
                    "status": "completed",
                },
            ],
        )],
        {"outcome": "completed"},
        [],
    ])

    assert await task_recovery.recover_orphan_tasks(object()) == 1
    assert recovery_db.calls[1][1]["p_content"] == [
        {"type": "text", "text": "分析中"},
        {
            "type": "tool_step",
            "tool_name": "query",
            "status": "completed",
        },
        {"type": "text", "text": "最终回答"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"accumulated_content": ""},
        {"accumulated_content": None},
        {"placeholder_message_id": None},
    ],
)
async def test_missing_content_or_placeholder_fails_task(
    recovery_db: _RecoveryDb,
    overrides: dict[str, Any],
) -> None:
    recovery_db.results.extend([
        [_task(**overrides)],
        {"outcome": "failed"},
        [],
    ])

    assert await task_recovery.recover_orphan_tasks(object()) == 0
    name, params = recovery_db.calls[1]
    assert name == "worker_fail_orphan_task"
    assert params["p_error_message"] == task_recovery._INTERRUPTED_ERROR


@pytest.mark.asyncio
async def test_claim_failure_is_non_fatal_and_returns_progress(
    recovery_db: _RecoveryDb,
) -> None:
    recovery_db.results.append(RuntimeError("database unavailable"))

    assert await task_recovery.recover_orphan_tasks(object()) == 0


@pytest.mark.asyncio
async def test_terminal_failure_leaves_claim_for_lease_recovery(
    recovery_db: _RecoveryDb,
) -> None:
    recovery_db.results.extend([
        [_task()],
        RuntimeError("transaction aborted"),
        [],
    ])

    assert await task_recovery.recover_orphan_tasks(object()) == 0
    assert [call[0] for call in recovery_db.calls] == [
        "worker_claim_orphan_tasks",
        "worker_complete_orphan_task",
        "worker_claim_orphan_tasks",
    ]


@pytest.mark.asyncio
async def test_non_committing_outcome_is_not_counted(
    recovery_db: _RecoveryDb,
) -> None:
    recovery_db.results.extend([
        [_task()],
        {"outcome": "ownership_lost"},
        [],
    ])

    assert await task_recovery.recover_orphan_tasks(object()) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_marker", [True, "true", None, 1, {}, []])
async def test_non_legacy_safe_runtime_marker_is_never_settled(
    recovery_db: _RecoveryDb,
    runtime_marker: Any,
) -> None:
    recovery_db.results.extend([
        [_task(delivery_context={"runtime": runtime_marker})],
        [],
    ])

    assert await task_recovery.recover_orphan_tasks(object()) == 0
    assert [call[0] for call in recovery_db.calls] == [
        "worker_claim_orphan_tasks",
        "worker_claim_orphan_tasks",
    ]


@pytest.mark.asyncio
async def test_canonical_runtime_false_remains_legacy_compatible(
    recovery_db: _RecoveryDb,
) -> None:
    recovery_db.results.extend([
        [_task(delivery_context={"runtime": False})],
        {"outcome": "completed"},
        [],
    ])

    assert await task_recovery.recover_orphan_tasks(object()) == 1
    assert recovery_db.calls[1][0] == "worker_complete_orphan_task"


def test_recovery_scope_is_actorless_worker() -> None:
    client = task_recovery._recovery_db(object())

    assert client.scope.actor_user_id is None
    assert client.scope.org_id is None
    assert client.scope.access_kind.value == "worker"
    assert client.scope.request_id == "orphan-task-recovery"
