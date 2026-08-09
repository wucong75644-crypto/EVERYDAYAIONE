"""AR-18-A1.2-B2 Web task-cancel routing contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.requests import Request

from api.deps import OrgContext
from core.exceptions import AppException
from services.agent.runtime.task_cancel import TaskOwner, classify_task_owner


class _Query:
    def __init__(self, database: "_Database", table: str) -> None:
        self.database = database
        self.table = table
        self.filters: dict[str, object] = {}
        self.update_values: dict[str, object] | None = None

    def select(self, _fields: str) -> "_Query":
        return self

    def eq(self, field: str, value: object) -> "_Query":
        self.filters[field] = value
        return self

    def is_(self, field: str, value: object) -> "_Query":
        self.filters[field] = None if value == "null" else value
        return self

    def in_(self, _field: str, _values: list[str]) -> "_Query":
        return self

    def single(self) -> "_Query":
        return self

    def update(self, values: dict[str, object]) -> "_Query":
        self.update_values = values
        return self

    def execute(self) -> SimpleNamespace:
        if self.update_values is not None:
            self.database.updates.append((self.table, self.update_values))
            return SimpleNamespace(data=[])
        if self.table != "tasks":
            return SimpleNamespace(data=[])
        matches = [
            row for row in self.database.tasks
            if all(row.get(field) == value for field, value in self.filters.items())
        ]
        if "external_task_id" in self.filters:
            return SimpleNamespace(data=matches[0] if matches else None)
        return SimpleNamespace(data=matches)


class _Rpc:
    def __init__(self, database: "_Database") -> None:
        self.database = database

    def execute(self) -> SimpleNamespace:
        if self.database.rpc_error is not None:
            raise self.database.rpc_error
        return SimpleNamespace(data=self.database.rpc_result)


class _Database:
    def __init__(
        self, tasks: list[dict[str, object]], *,
        outcome: object = "cancelled",
        rpc_error: Exception | None = None,
    ) -> None:
        self.tasks = tasks
        self.rpc_result = (
            {"outcome": outcome} if isinstance(outcome, str) else outcome
        )
        self.rpc_error = rpc_error
        self.rpc_calls: list[tuple[str, dict[str, object]]] = []
        self.updates: list[tuple[str, dict[str, object]]] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, name: str, params: dict[str, object]) -> _Rpc:
        self.rpc_calls.append((name, params))
        return _Rpc(self)


class _SqlstateError(Exception):
    sqlstate = "42501"


def _request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/",
        "headers": [], "query_string": b"",
    })


def _runtime_task(**overrides: object) -> dict[str, object]:
    task_id = str(uuid4())
    user_id = str(uuid4())
    row: dict[str, object] = {
        "id": task_id,
        "external_task_id": "external-1",
        "client_task_id": "client-1",
        "user_id": user_id,
        "conversation_id": str(uuid4()),
        "org_id": None,
        "placeholder_message_id": "placeholder-1",
        "assistant_message_id": str(uuid4()),
        "request_params": {"_task_slot_id": "slot-1"},
        "delivery_context": {
            "actor": False,
            "runtime": True,
            "runtime_session_id": str(uuid4()),
            "runtime_command_id": str(uuid4()),
        },
    }
    row.update(overrides)
    return row


async def _cancel(
    task: dict[str, object], database: _Database, message_id: str,
) -> tuple[dict[str, object], AsyncMock, MagicMock, MagicMock]:
    from api.routes.task import cancel_task_by_message_id

    release = AsyncMock()
    anchor = MagicMock()
    websocket = MagicMock()
    with patch("api.routes.task.release_task_slot", release), patch(
        "api.routes.task._anchor_messages_immediately", anchor,
    ), patch("services.websocket_manager.ws_manager", websocket):
        result = await cancel_task_by_message_id(
            request=_request(),
            ctx=OrgContext(
                user_id=str(task["user_id"]),
                org_id=task.get("org_id"),
            ),
            db=database,
            message_id=message_id,
        )
    return result, release, anchor, websocket


@pytest.mark.parametrize(
    ("context", "owner"), [
        ({"runtime": True, "actor": False}, TaskOwner.RUNTIME),
        ('{"runtime":true,"actor":false}', TaskOwner.RUNTIME),
        ({"runtime": False, "actor": True}, TaskOwner.ACTOR),
        ({"actor": True}, TaskOwner.ACTOR),
        ('{"actor":true}', TaskOwner.ACTOR),
        ({"channel": "web"}, TaskOwner.LEGACY),
        (None, TaskOwner.LEGACY),
        ({"runtime": True, "actor": True}, TaskOwner.AMBIGUOUS),
        ({"runtime": "true", "actor": False}, TaskOwner.AMBIGUOUS),
        ({"runtime": None, "actor": False}, TaskOwner.AMBIGUOUS),
        ({"runtime": 1, "actor": False}, TaskOwner.AMBIGUOUS),
        ({"runtime": {}, "actor": False}, TaskOwner.AMBIGUOUS),
        ({"runtime": True}, TaskOwner.AMBIGUOUS),
        ({"actor": False}, TaskOwner.AMBIGUOUS),
        ([], TaskOwner.AMBIGUOUS),
    ],
)
def test_owner_classification_is_canonical_and_fail_closed(
    context: object, owner: TaskOwner,
) -> None:
    assert classify_task_owner({"delivery_context": context}) is owner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["cancelled_before_claim", "cancelled", "already_cancelled"],
)
async def test_runtime_success_and_replay_use_v2_then_side_effects(
    outcome: str,
) -> None:
    task = _runtime_task()
    database = _Database([task], outcome=outcome)

    result, release, anchor, websocket = await _cancel(
        task, database, str(task["assistant_message_id"]),
    )

    assert result == {"success": True, "cancelled_count": 1}
    assert database.updates == []
    assert database.rpc_calls[0][0] == "request_agent_runtime_task_cancel_v2"
    params = database.rpc_calls[0][1]
    assert params["p_message_id"] == task["assistant_message_id"]
    assert "p_request_hash" not in params
    release.assert_awaited_once_with(task)
    anchor.assert_called_once_with(
        database, task["assistant_message_id"],
        conversation_id=task["conversation_id"],
    )
    websocket.cancel_task.assert_called_once_with("client-1", org_id=None)


@pytest.mark.asyncio
async def test_placeholder_alias_uses_canonical_assistant_and_stable_key() -> None:
    task = _runtime_task()
    database = _Database([task], outcome="already_cancelled")

    await _cancel(task, database, "placeholder-1")
    await _cancel(task, database, "placeholder-1")

    first = database.rpc_calls[0][1]
    second = database.rpc_calls[1][1]
    assert first["p_message_id"] == task["assistant_message_id"]
    assert first["p_idempotency_key"] == second["p_idempotency_key"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "error", "status", "code"), [
        ("terminal_conflict", None, 409, "RUNTIME_TASK_CANCEL_CONFLICT"),
        ("idempotency_conflict", None, 409, "RUNTIME_TASK_CANCEL_CONFLICT"),
        (None, _SqlstateError("binding rejected"), 409,
         "RUNTIME_TASK_CANCEL_CONFLICT"),
        ({"unexpected": "receipt"}, None, 503,
         "RUNTIME_TASK_CANCEL_UNAVAILABLE"),
        (None, RuntimeError("provider-secret-must-not-log"), 503,
         "RUNTIME_TASK_CANCEL_UNAVAILABLE"),
    ],
)
async def test_runtime_conflict_or_unknown_has_no_followup_side_effects(
    outcome: object, error: Exception | None, status: int, code: str,
) -> None:
    from api.routes.task import cancel_task_by_message_id

    task = _runtime_task()
    database = _Database([task], outcome=outcome, rpc_error=error)
    release = AsyncMock()
    anchor = MagicMock()
    websocket = MagicMock()
    with patch("api.routes.task.release_task_slot", release), patch(
        "api.routes.task._anchor_messages_immediately", anchor,
    ), patch("services.websocket_manager.ws_manager", websocket), patch(
        "api.routes.task.logger.error",
    ) as error_log, pytest.raises(AppException) as raised:
        await cancel_task_by_message_id(
            request=_request(),
            ctx=OrgContext(user_id=str(task["user_id"])),
            db=database,
            message_id=str(task["assistant_message_id"]),
        )

    assert raised.value.status_code == status
    assert raised.value.code == code
    assert database.updates == []
    release.assert_not_awaited()
    anchor.assert_not_called()
    websocket.cancel_task.assert_not_called()
    error_log.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["user_id", "org_id"])
async def test_runtime_tenant_and_user_mismatch_do_not_call_facade(
    field: str,
) -> None:
    from api.routes.task import cancel_task_by_message_id

    task = _runtime_task(org_id=str(uuid4()) if field == "org_id" else None)
    database = _Database([task])
    ctx = OrgContext(
        user_id=str(uuid4()) if field == "user_id" else str(task["user_id"]),
        org_id=str(uuid4()) if field == "org_id" else task.get("org_id"),
    )
    # Simulate a compromised/read-bug response that escaped route predicates.
    database.tasks[0][field] = task[field]
    with patch.object(_Query, "execute", return_value=SimpleNamespace(data=[task])), \
         pytest.raises(AppException) as raised:
        await cancel_task_by_message_id(
            request=_request(), ctx=ctx, db=database,
            message_id=str(task["assistant_message_id"]),
        )

    assert raised.value.status_code == 409
    assert database.rpc_calls == []


@pytest.mark.asyncio
async def test_actor_and_legacy_keep_their_existing_cancel_paths() -> None:
    actor = _runtime_task(delivery_context={"actor": True})
    actor_db = _Database([actor])
    with patch(
        "services.conversation_task.cancel_actor_task", return_value=True,
    ) as actor_cancel:
        result, _, _, _ = await _cancel(
            actor, actor_db, str(actor["assistant_message_id"]),
        )
    assert result["cancelled_count"] == 1
    actor_cancel.assert_called_once()
    assert actor_db.rpc_calls == []
    assert actor_db.updates == []

    legacy = _runtime_task(delivery_context={"channel": "web"})
    legacy_db = _Database([legacy])
    result, _, _, _ = await _cancel(
        legacy, legacy_db, str(legacy["assistant_message_id"]),
    )
    assert result["cancelled_count"] == 1
    assert legacy_db.rpc_calls == []
    assert legacy_db.updates[0][0] == "tasks"


@pytest.mark.asyncio
async def test_ambiguous_owner_never_falls_back_to_legacy_update() -> None:
    from api.routes.task import cancel_task_by_message_id

    task = _runtime_task(delivery_context={"runtime": True, "actor": True})
    database = _Database([task])
    with pytest.raises(AppException) as raised:
        await cancel_task_by_message_id(
            request=_request(), ctx=OrgContext(user_id=str(task["user_id"])),
            db=database, message_id=str(task["assistant_message_id"]),
        )
    assert raised.value.code == "TASK_OWNER_MARKER_INVALID"
    assert database.updates == []
    assert database.rpc_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery_context", [
        {"runtime": True, "actor": False},
        {"runtime": True, "actor": True},
        {"runtime": "true", "actor": False},
    ],
)
async def test_manual_fail_forbids_runtime_and_ambiguous_without_release(
    delivery_context: object,
) -> None:
    from api.routes.task import MarkTaskFailedRequest, mark_task_failed

    task = _runtime_task(delivery_context=delivery_context)
    database = _Database([task])
    release = AsyncMock()
    with patch("api.routes.task.release_task_slot", release), \
         pytest.raises(AppException) as raised:
        await mark_task_failed.__wrapped__(
            request=_request(), ctx=OrgContext(user_id=str(task["user_id"])),
            db=database, external_task_id=str(task["external_task_id"]),
            body=MarkTaskFailedRequest(reason="manual"),
        )
    assert raised.value.status_code == 409
    assert raised.value.code == "RUNTIME_TASK_MANUAL_FAIL_FORBIDDEN"
    assert database.updates == []
    release.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery_context", [{"actor": True}, {"channel": "legacy"}],
)
async def test_manual_fail_preserves_actor_and_legacy_behavior(
    delivery_context: object,
) -> None:
    from api.routes.task import MarkTaskFailedRequest, mark_task_failed

    task = _runtime_task(delivery_context=delivery_context)
    database = _Database([task])
    release = AsyncMock()
    with patch("api.routes.task.release_task_slot", release):
        result = await mark_task_failed.__wrapped__(
            request=_request(), ctx=OrgContext(user_id=str(task["user_id"])),
            db=database, external_task_id=str(task["external_task_id"]),
            body=MarkTaskFailedRequest(reason="manual"),
        )
    assert result["success"] is True
    assert database.updates[0][0] == "tasks"
    release.assert_awaited_once_with(task)
