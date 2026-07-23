"""WebSocket 订阅入口的租户门禁测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from api.routes.ws import _handle_message


@pytest.mark.asyncio
async def test_subscription_rejects_task_outside_connection_scope() -> None:
    with (
        patch(
            "api.routes.ws.find_task_in_connection_scope",
            return_value=None,
        ),
        patch("api.routes.ws.ws_manager") as manager,
    ):
        manager.send_to_connection = AsyncMock()
        manager.subscribe_task = AsyncMock()

        await _handle_message(
            "conn-1",
            "user-1",
            "org-a",
            {"type": "subscribe", "payload": {"task_id": "task-b"}},
        )

    manager.subscribe_task.assert_not_awaited()
    error_message = manager.send_to_connection.await_args.args[1]
    assert error_message["payload"]["code"] == "TASK_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_subscription_registers_task_in_connection_scope() -> None:
    task = {
        "id": "task-a",
        "type": "chat",
        "status": "running",
        "accumulated_content": "已恢复",
        "accumulated_blocks": [],
    }
    with (
        patch(
            "api.routes.ws.find_task_in_connection_scope",
            return_value=task,
        ),
        patch("api.routes.ws.ws_manager") as manager,
    ):
        manager.subscribe_task = AsyncMock(return_value=True)
        manager.send_to_connection = AsyncMock()

        await _handle_message(
            "conn-1",
            "user-1",
            "org-a",
            {"type": "subscribe", "payload": {"task_id": "task-a"}},
        )

    manager.subscribe_task.assert_awaited_once_with("conn-1", "task-a")
    subscribed = manager.send_to_connection.await_args.args[1]
    assert subscribed["type"] == "subscribed"
    assert subscribed["payload"]["accumulated"] == "已恢复"


@pytest.mark.asyncio
async def test_steer_rejects_task_outside_connection_scope() -> None:
    with (
        patch(
            "api.routes.ws.find_task_in_connection_scope",
            return_value=None,
        ),
        patch("api.routes.ws.ws_manager") as manager,
    ):
        manager.send_to_connection = AsyncMock()

        await _handle_message(
            "conn-1",
            "user-1",
            None,
            {
                "type": "user_steer",
                "payload": {"task_id": "enterprise-task", "message": "继续"},
            },
        )

    manager.resolve_steer.assert_not_called()
    error_message = manager.send_to_connection.await_args.args[1]
    assert error_message["payload"]["code"] == "TASK_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_confirm_response_uses_connection_identity() -> None:
    with patch("api.routes.ws.ws_manager") as manager:
        manager.resolve_confirm = AsyncMock(return_value=True)

        await _handle_message(
            "conn-1",
            "user-1",
            "org-a",
            {
                "type": "tool_confirm_response",
                "payload": {"tool_call_id": "tc-1", "approved": True},
            },
        )

    manager.resolve_confirm.assert_called_once_with(
        "tc-1", "user-1", "org-a", True,
    )


@pytest.mark.asyncio
async def test_steer_resolution_uses_connection_org() -> None:
    with (
        patch(
            "api.routes.ws.find_task_in_connection_scope",
            return_value={"id": "task-1"},
        ),
        patch("api.routes.ws.ws_manager") as manager,
    ):
        manager.resolve_steer.return_value = True

        await _handle_message(
            "conn-1",
            "user-1",
            "org-a",
            {
                "type": "user_steer",
                "payload": {"task_id": "task-1", "message": "继续"},
            },
        )

    manager.resolve_steer.assert_called_once_with(
        "task-1", "继续", org_id="org-a",
    )
