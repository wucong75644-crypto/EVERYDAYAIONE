"""AR-18-A1.2-B2 alias union and partial-result regression tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from api.deps import OrgContext
from core.exceptions import AppException
from tests.test_agent_runtime_web_task_cancel import (
    _Database,
    _request,
    _runtime_task,
)


async def _cancel_many(
    tasks: list[dict[str, object]], database: _Database, message_id: str,
    *, release: AsyncMock | None = None, anchor: MagicMock | None = None,
    websocket: MagicMock | None = None,
) -> tuple[dict[str, object], AsyncMock, MagicMock, MagicMock]:
    from api.routes.task import cancel_task_by_message_id

    release = release or AsyncMock()
    anchor = anchor or MagicMock(return_value=True)
    websocket = websocket or MagicMock()
    with patch("api.routes.task.release_task_slot", release), patch(
        "api.routes.task._anchor_messages_immediately", anchor,
    ), patch("services.websocket_manager.ws_manager", websocket):
        result = await cancel_task_by_message_id(
            request=_request(),
            ctx=OrgContext(
                user_id=str(tasks[0]["user_id"]),
                org_id=tasks[0].get("org_id"),
            ),
            db=database,
            message_id=message_id,
        )
    return result, release, anchor, websocket


@pytest.mark.asyncio
async def test_cross_alias_duplicate_task_is_cancelled_once() -> None:
    alias = str(uuid4())
    task = _runtime_task(
        placeholder_message_id=alias,
        assistant_message_id=alias,
    )
    database = _Database([task])

    result, release, anchor, _ = await _cancel_many([task], database, alias)

    assert result["cancelled_count"] == 1
    assert len(database.rpc_calls) == 1
    release.assert_awaited_once_with(task)
    anchor.assert_called_once()


@pytest.mark.asyncio
async def test_cross_alias_distinct_tasks_are_both_cancelled() -> None:
    alias = str(uuid4())
    first = _runtime_task(placeholder_message_id=alias)
    second = _runtime_task(
        user_id=first["user_id"],
        placeholder_message_id="other-placeholder",
        assistant_message_id=alias,
    )
    database = _Database([first, second])

    result, release, anchor, _ = await _cancel_many(
        [first, second], database, alias,
    )

    assert result["cancelled_count"] == 2
    assert len(database.rpc_calls) == 2
    assert release.await_count == 2
    assert anchor.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_result", "failure_code"), [
        ({"outcome": "terminal_conflict"}, "RUNTIME_TASK_CANCEL_CONFLICT"),
        (RuntimeError("database-secret"), "RUNTIME_TASK_CANCEL_UNAVAILABLE"),
    ],
)
async def test_durable_success_then_runtime_failure_returns_partial_200(
    second_result: object, failure_code: str,
) -> None:
    alias = str(uuid4())
    first = _runtime_task(placeholder_message_id=alias)
    second = _runtime_task(
        user_id=first["user_id"], assistant_message_id=alias,
        placeholder_message_id="other-placeholder",
    )
    database = _Database([first, second])
    database.rpc_sequence = [{"outcome": "cancelled"}, second_result]

    result, release, anchor, _ = await _cancel_many(
        [first, second], database, alias,
    )

    assert result == {
        "success": True,
        "cancelled_count": 1,
        "failed_count": 1,
        "partial": True,
        "failure_codes": [failure_code],
        "followup_failed_count": 0,
    }
    release.assert_awaited_once_with(first)
    anchor.assert_called_once()
    assert "secret" not in str(result).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rpc_result", "status", "code"), [
        ({"outcome": "terminal_conflict"}, 409,
         "RUNTIME_TASK_CANCEL_CONFLICT"),
        (RuntimeError("database-secret"), 503,
         "RUNTIME_TASK_CANCEL_UNAVAILABLE"),
    ],
)
async def test_all_runtime_failures_keep_error_status(
    rpc_result: object, status: int, code: str,
) -> None:
    from api.routes.task import cancel_task_by_message_id

    alias = str(uuid4())
    first = _runtime_task(placeholder_message_id=alias)
    second = _runtime_task(
        user_id=first["user_id"], assistant_message_id=alias,
        placeholder_message_id="other-placeholder",
    )
    database = _Database([first, second])
    database.rpc_sequence = [rpc_result, rpc_result]
    release = AsyncMock()

    with patch("api.routes.task.release_task_slot", release), \
         pytest.raises(AppException) as raised:
        await cancel_task_by_message_id(
            request=_request(),
            ctx=OrgContext(user_id=str(first["user_id"])),
            db=database,
            message_id=alias,
        )

    assert raised.value.status_code == status
    assert raised.value.code == code
    assert len(database.rpc_calls) == 2
    assert database.updates == []
    release.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("followup", "failure_code"), [
        ("ws", "WS_CANCEL_FAILED"),
        ("slot", "SLOT_RELEASE_FAILED"),
        ("anchor", "ANCHOR_FAILED"),
    ],
)
async def test_durable_success_followup_failure_returns_partial_200(
    followup: str, failure_code: str,
) -> None:
    task = _runtime_task()
    database = _Database([task])
    release = AsyncMock()
    anchor = MagicMock(return_value=True)
    websocket = MagicMock()
    if followup == "ws":
        websocket.cancel_task.side_effect = RuntimeError("ws-secret")
    elif followup == "slot":
        release.side_effect = RuntimeError("slot-secret")
    else:
        anchor.side_effect = RuntimeError("anchor-secret")

    with patch("services.web_task_cancel.logger.warning") as warning_log:
        result, _, _, _ = await _cancel_many(
            [task], database, str(task["assistant_message_id"]),
            release=release, anchor=anchor, websocket=websocket,
        )

    assert result["success"] is True
    assert result["cancelled_count"] == 1
    assert result["failed_count"] == 0
    assert result["partial"] is True
    assert result["failure_codes"] == [failure_code]
    assert result["followup_failed_count"] == 1
    assert "secret" not in str(result).lower()
    assert "secret" not in str(warning_log.call_args_list).lower()


@pytest.mark.asyncio
async def test_ambiguous_owner_in_union_blocks_every_mutation() -> None:
    from api.routes.task import cancel_task_by_message_id

    alias = str(uuid4())
    valid = _runtime_task(placeholder_message_id=alias)
    ambiguous = _runtime_task(
        user_id=valid["user_id"],
        placeholder_message_id="other-placeholder",
        assistant_message_id=alias,
        delivery_context={"runtime": True, "actor": True},
    )
    database = _Database([valid, ambiguous])
    release = AsyncMock()
    anchor = MagicMock()
    websocket = MagicMock()
    with patch("api.routes.task.release_task_slot", release), patch(
        "api.routes.task._anchor_messages_immediately", anchor,
    ), patch("services.websocket_manager.ws_manager", websocket), \
         pytest.raises(AppException) as raised:
        await cancel_task_by_message_id(
            request=_request(),
            ctx=OrgContext(user_id=str(valid["user_id"])),
            db=database,
            message_id=alias,
        )

    assert raised.value.code == "TASK_OWNER_MARKER_INVALID"
    assert database.rpc_calls == []
    assert database.updates == []
    release.assert_not_awaited()
    anchor.assert_not_called()
    websocket.cancel_task.assert_not_called()


@pytest.mark.asyncio
async def test_later_local_binding_failure_blocks_every_mutation() -> None:
    from api.routes.task import cancel_task_by_message_id

    alias = str(uuid4())
    valid = _runtime_task(placeholder_message_id=alias)
    invalid = _runtime_task(
        user_id=valid["user_id"],
        placeholder_message_id="other-placeholder",
        assistant_message_id=alias,
    )
    invalid["delivery_context"]["runtime_session_id"] = "not-a-uuid"
    database = _Database([valid, invalid])
    release = AsyncMock()
    with patch("api.routes.task.release_task_slot", release), \
         pytest.raises(AppException) as raised:
        await cancel_task_by_message_id(
            request=_request(),
            ctx=OrgContext(user_id=str(valid["user_id"])),
            db=database,
            message_id=alias,
        )

    assert raised.value.code == "RUNTIME_TASK_CANCEL_CONFLICT"
    assert database.rpc_calls == []
    assert database.updates == []
    release.assert_not_awaited()
