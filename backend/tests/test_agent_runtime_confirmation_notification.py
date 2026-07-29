from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

from services.agent.runtime.application.confirmation_notification import (
    ToolConfirmationNotificationWorker,
)
from services.tool_confirmation.types import (
    ConfirmationBinding, ConfirmationRequest,
)


def test_runtime_action_snapshot_binds_registry_safety_level():
    from services.agent.runtime.production_model import _actions

    _, actions = _actions(SimpleNamespace(tool_calls=(
        SimpleNamespace(
            index=0, call_id="call-1", provider_call_id=None,
            name="code_execute",
            arguments_json='{"code":"print(1)","runtime":"python"}',
        ),
    )), "run-1")

    assert actions[0]["policy_snapshot"] == {
        "source": "runtime_executor_registry",
        "safety_level": "dangerous",
    }


def _response(data):
    response = MagicMock()
    response.data = data
    return response


@pytest.mark.asyncio
async def test_notification_binds_persisted_interaction_before_delivery():
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    claim = {
        "outcome": "claimed",
        "notification_token": "f67bec0a-400c-4713-91f3-123d7472d763",
        "interaction_id": "0ade5f65-bfe9-4592-8319-7c04dc4579fc",
        "interaction_version": 0,
        "authorization_expires_at": expires_at,
        "action_id": "ce9380ec-3691-45ca-a6e4-a5445df869b4",
        "task_id": "f8ad4a64-5b53-4b2c-bc94-b57363829518",
        "conversation_id": "1600d848-5728-4be3-9229-bf37cfab8b02",
        "tool_call_id": "call-1",
        "tool_name": "code_execute",
        "arguments": {"code": "print(1)", "runtime": "python"},
        "arguments_hash":
            "6b909adab472dcf3ac23ab99005d193bd39cd38198a1790c67703701ee2cae49",
        "user_id": "a82a3f1c-c763-461a-b3a5-3e130752aaf4",
        "org_id": "81dd6b01-f7bf-4d5c-94b4-99bf5047052b",
    }
    # Use the implementation's canonical hash to keep the fixture explicit.
    from services.tool_confirmation.canonical import canonical_arguments_hash
    claim["arguments_hash"] = canonical_arguments_hash(claim["arguments"])
    database = MagicMock()
    database.rpc.side_effect = [
        MagicMock(execute=AsyncMock(return_value=_response(claim))),
        MagicMock(execute=AsyncMock(return_value=_response(
            {"outcome": "completed"},
        ))),
    ]
    binding = ConfirmationBinding(
        claim["action_id"], claim["interaction_id"], 0, claim["task_id"],
        claim["tool_call_id"], claim["tool_name"], claim["arguments_hash"],
        claim["user_id"], claim["org_id"], expires_at,
    )
    request = ConfirmationRequest("c" * 40, "waiter", binding, {}, "dangerous")
    service = MagicMock(create=AsyncMock(return_value=request))
    websocket = MagicMock(send_tool_confirmation=AsyncMock(return_value=True))
    worker = ToolConfirmationNotificationWorker(
        database=database, service=service,
        websocket_manager=websocket, worker_id="projection-1",
    )

    assert await worker.run_once() is True
    service.create.assert_awaited_once()
    websocket.send_tool_confirmation.assert_awaited_once()
    assert database.rpc.call_args_list[1].args[0] == (
        "complete_agent_tool_confirmation_notification"
    )
    assert database.rpc.call_args_list[1].args[1]["p_delivered"] is True


@pytest.mark.asyncio
async def test_notification_hash_mismatch_releases_claim_without_delivery():
    database = MagicMock()
    database.rpc.side_effect = [
        MagicMock(execute=AsyncMock(return_value=_response({
            "outcome": "claimed", "notification_token": "token",
            "interaction_id": "interaction", "interaction_version": 0,
            "authorization_expires_at":
                datetime.now(timezone.utc) + timedelta(minutes=5),
            "action_id": "action", "task_id": "task",
            "conversation_id": "conversation", "tool_call_id": "call",
            "tool_name": "code_execute", "arguments": {"code": "x"},
            "arguments_hash": "0" * 64, "user_id": "user", "org_id": "org",
        }))),
        MagicMock(execute=AsyncMock(return_value=_response(
            {"outcome": "released"},
        ))),
    ]
    service = MagicMock()
    websocket = MagicMock()
    worker = ToolConfirmationNotificationWorker(
        database=database, service=service,
        websocket_manager=websocket, worker_id="projection-1",
    )

    with pytest.raises(
        RuntimeError, match="TOOL_CONFIRMATION_ARGUMENTS_HASH_MISMATCH",
    ):
        await worker.run_once()
    service.create.assert_not_called()
    websocket.send_tool_confirmation.assert_not_called()
