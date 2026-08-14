from types import SimpleNamespace

import pytest

from core.exceptions import AppException
from services.runtime_media_message_control import RuntimeMediaMessageControlService


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.task_id = None

    def select(self, _fields):
        return self

    def eq(self, field, value):
        if field == "id":
            self.task_id = value
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows.get(self.task_id))


class _RPC:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


class _DB:
    def __init__(self, receipt, tasks=None):
        self.receipt = receipt
        self.tasks = tasks or {}
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        if isinstance(self.receipt, Exception):
            return _RPC(error=self.receipt)
        return _RPC(data=self.receipt)

    def table(self, name):
        assert name == "tasks"
        return _Query(self.tasks)


@pytest.mark.asyncio
async def test_cancel_preserves_reconcile_outcome_and_releases_only_receipt_tasks() -> None:
    receipt = {
        "outcome": "cancel_requested",
        "cancelled_count": 1,
        "reconcile_count": 2,
        "completed_count": 3,
        "release_task_ids": ["chat-task", "queued-task"],
    }
    db = _DB(receipt, tasks={
        "chat-task": {"id": "chat-task"},
        "queued-task": {"id": "queued-task"},
    })
    released = []

    async def release(task):
        released.append(task["id"])

    service = RuntimeMediaMessageControlService(db, user_id="user", org_id="org")
    result = await service.cancel_message(
        "message", idempotency_key="cancel-1", release_slot=release,
    )

    assert result == {
        "success": True,
        "runtime_media": True,
        "outcome": "cancel_requested",
        "cancelled_count": 1,
        "reconcile_count": 2,
        "completed_count": 3,
        "partial": True,
    }
    assert released == ["chat-task", "queued-task"]


@pytest.mark.asyncio
async def test_retry_passes_complete_scope_and_returns_stable_slot_receipt() -> None:
    db = _DB({
        "outcome": "created", "action_id": "action", "run_id": "run",
        "task_id": "task", "slot_id": "slot", "slot_index": 7,
        "slot_revision": 4,
    })
    service = RuntimeMediaMessageControlService(db, user_id="user", org_id="org")

    result = await service.retry_slot(
        "message", "conversation", 7, slot_id="slot",
        expected_slot_revision=3, idempotency_key="retry-1",
        client_task_id="client-task", task_slot_id="limit-slot",
    )

    assert result and result.action_id == "action" and result.slot_revision == 4
    name, params = db.calls[0]
    assert name == "retry_agent_runtime_media_slot_v1"
    assert params == {
        "p_output_message_id": "message",
        "p_conversation_id": "conversation",
        "p_slot_index": 7,
        "p_slot_id": "slot",
        "p_expected_slot_revision": 3,
        "p_org_id": "org",
        "p_user_id": "user",
        "p_idempotency_key": "retry-1",
        "p_client_task_id": "client-task",
        "p_task_slot_id": "limit-slot",
    }


@pytest.mark.asyncio
async def test_retry_rejects_active_slot_without_falling_back() -> None:
    service = RuntimeMediaMessageControlService(
        _DB({"outcome": "slot_active"}), user_id="user", org_id="org",
    )
    with pytest.raises(AppException) as error:
        await service.retry_slot(
            "message", "conversation", 0, slot_id="slot",
            expected_slot_revision=1, idempotency_key="retry-1",
            client_task_id=None, task_slot_id=None,
        )
    assert error.value.code == "RUNTIME_MEDIA_SLOT_ACTIVE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        ("projection_pending", "RUNTIME_MEDIA_PROJECTION_PENDING"),
        ("slot_conflict", "RUNTIME_MEDIA_SLOT_CONFLICT"),
    ],
)
async def test_retry_surfaces_projection_and_slot_conflicts(
    outcome: str, code: str,
) -> None:
    service = RuntimeMediaMessageControlService(
        _DB({"outcome": outcome}), user_id="user", org_id="org",
    )
    with pytest.raises(AppException) as error:
        await service.retry_slot(
            "message", "conversation", 0, slot_id="slot",
            expected_slot_revision=1, idempotency_key="retry-1",
            client_task_id=None, task_slot_id=None,
        )
    assert error.value.code == code


@pytest.mark.asyncio
async def test_non_runtime_message_can_fall_back() -> None:
    service = RuntimeMediaMessageControlService(
        _DB({"outcome": "not_runtime_media"}), user_id="user", org_id="org",
    )
    result = await service.retry_slot(
        "message", "conversation", 0, slot_id="slot",
        expected_slot_revision=0, idempotency_key="retry-1",
        client_task_id=None, task_slot_id=None,
    )
    assert result is None
