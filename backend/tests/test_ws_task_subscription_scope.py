"""WebSocket 订阅入口的租户门禁测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

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
            MagicMock(),
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
            MagicMock(),
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
            MagicMock(),
        )

    manager.resolve_steer.assert_not_called()
    error_message = manager.send_to_connection.await_args.args[1]
    assert error_message["payload"]["code"] == "TASK_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_legacy_confirm_response_is_rejected() -> None:
    with patch("api.routes.ws.ws_manager") as manager:
        manager.send_to_connection = AsyncMock()

        await _handle_message(
            "conn-1",
            "user-1",
            "org-a",
            {
                "type": "tool_confirm_response",
                "payload": {"tool_call_id": "tc-1", "approved": True},
            },
        )

    message = manager.send_to_connection.await_args.args[1]
    assert message["payload"]["code"] == "TOOL_CONFIRM_PROTOCOL_OBSOLETE"


@pytest.mark.asyncio
async def test_v3_confirm_response_uses_authenticated_actor() -> None:
    service = MagicMock()
    service.consume_response = AsyncMock(return_value="WON:APPROVED")
    confirmation_id = "c" * 43
    with (
        patch("api.routes.ws.ws_manager") as manager,
        patch("services.tool_confirmation.tool_confirmation_service", service),
    ):
        manager.send_to_connection = AsyncMock()
        await _handle_message(
            "conn-1", "user-1", "org-a",
            {"type": "tool_confirm_response", "payload": {
                "confirmation_id": confirmation_id, "approved": True,
            }},
        )
    service.consume_response.assert_awaited_once_with(
        confirmation_id=confirmation_id, user_id="user-1",
        org_id="org-a", approved=True,
    )
    manager.send_to_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_actor_does_not_clear_disconnect_tracking() -> None:
    service = MagicMock()
    service.consume_response = AsyncMock(return_value="ACTOR_MISMATCH")
    with (
        patch("api.routes.ws.ws_manager") as manager,
        patch("services.tool_confirmation.tool_confirmation_service", service),
    ):
        manager.send_to_connection = AsyncMock()
        await _handle_message(
            "conn-1", "user-1", "wrong-org",
            {"type": "tool_confirm_response", "payload": {
                "confirmation_id": "c" * 43, "approved": True,
            }},
        )
    manager.forget_confirmation_delivery.assert_not_called()
    error = manager.send_to_connection.await_args.args[1]
    assert error["payload"]["code"] == "ACTOR_MISMATCH"


@pytest.mark.asyncio
async def test_v3_confirm_response_rejects_coerced_boolean() -> None:
    with patch("api.routes.ws.ws_manager") as manager:
        manager.send_to_connection = AsyncMock()
        await _handle_message(
            "conn-1", "user-1", "org-a",
            {"type": "tool_confirm_response", "payload": {
                "confirmation_id": "c" * 43, "approved": "true",
            }},
        )
    message = manager.send_to_connection.await_args.args[1]
    assert message["payload"]["code"] == "MALFORMED_TOOL_CONFIRM_RESPONSE"


@pytest.mark.asyncio
async def test_v3_confirm_response_rejects_legacy_extra_field() -> None:
    service = MagicMock()
    service.consume_response = AsyncMock()
    with (
        patch("api.routes.ws.ws_manager") as manager,
        patch("services.tool_confirmation.tool_confirmation_service", service),
    ):
        manager.send_to_connection = AsyncMock()
        await _handle_message(
            "conn-1", "user-1", "org-a",
            {"type": "tool_confirm_response", "payload": {
                "confirmation_id": "c" * 43,
                "approved": True,
                "tool_call_id": "legacy",
            }},
        )
    service.consume_response.assert_not_awaited()
    message = manager.send_to_connection.await_args.args[1]
    assert message["payload"]["code"] == "MALFORMED_TOOL_CONFIRM_RESPONSE"


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
            MagicMock(),
        )

    manager.resolve_steer.assert_called_once_with(
        "task-1", "继续", org_id="org-a",
    )
