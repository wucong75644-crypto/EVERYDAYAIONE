"""WebSocket 工具确认的任务归属与 Actor 持久化边界测试。"""

from __future__ import annotations

from types import SimpleNamespace
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes import ws
from services.conversation_commands import CommandType, ConversationCommand
from services.handlers.chat_tool_mixin import ChatToolMixin


class _Query:
    def __init__(self, row):
        self._row = row

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._row)


class _DB:
    def __init__(self, row):
        self.row = row

    def table(self, _name):
        return _Query(self.row)


def _task(*, actor: bool, conversation_id: str = "conversation-1"):
    return {
        "id": "task-1",
        "type": "chat",
        "status": "running",
        "conversation_id": conversation_id,
        "turn_id": "turn-1",
        "delivery_context": {"actor": actor},
    }


@pytest.mark.asyncio
async def test_actor_confirmation_is_persisted_with_scope():
    db = _DB(_task(actor=True))
    store = AsyncMock()
    store.append.return_value = {
        "outcome": "enqueued",
        "already_enqueued": False,
    }

    with patch.object(ws, "get_async_db", new=AsyncMock(return_value=db)), \
         patch.object(ws, "DatabaseConversationCommandStore", return_value=store):
        persisted, code = await ws._persist_actor_tool_confirmation(
            user_id="user-1",
            task_id="task-1",
            conversation_id="conversation-1",
            tool_call_id="tool-1",
            approved=True,
        )

    assert persisted is True
    assert code == ""
    store.append.assert_awaited_once()
    kwargs = store.append.await_args.kwargs
    assert kwargs["command_type"].value == "approval_result"
    assert kwargs["dedupe_key"] == "approval:tool-1"
    assert kwargs["payload"]["approved"] is True


@pytest.mark.asyncio
async def test_non_actor_confirmation_keeps_legacy_path():
    db = _DB(_task(actor=False))

    with patch.object(ws, "get_async_db", new=AsyncMock(return_value=db)):
        persisted, code = await ws._persist_actor_tool_confirmation(
            user_id="user-1",
            task_id="task-1",
            conversation_id="conversation-1",
            tool_call_id="tool-1",
            approved=False,
        )

    assert persisted is False
    assert code == "CONFIRM_LEGACY"


@pytest.mark.asyncio
async def test_actor_confirmation_rejects_wrong_conversation():
    db = _DB(_task(actor=True, conversation_id="conversation-1"))

    with patch.object(ws, "get_async_db", new=AsyncMock(return_value=db)):
        persisted, code = await ws._persist_actor_tool_confirmation(
            user_id="user-1",
            task_id="task-1",
            conversation_id="conversation-evil",
            tool_call_id="tool-1",
            approved=True,
        )

    assert persisted is False
    assert code == "CONFIRM_SCOPE_INVALID"


@pytest.mark.asyncio
async def test_durable_approval_returns_to_runtime_inbox_before_safe_point():
    command = ConversationCommand(
        command_id="event-1",
        event_id="event-1",
        command_type=CommandType.APPROVAL_RESULT,
        conversation_id="conversation-1",
        task_id="task-1",
        payload={"tool_call_id": "tool-1", "approved": True},
    )
    store = MagicMock()
    store.load_pending = AsyncMock(return_value=[command])
    store.acknowledge = AsyncMock()
    runtime = MagicMock()
    mixin = MagicMock()
    mixin._actor_cancellation_event = asyncio.Event()

    approved = await ChatToolMixin._poll_durable_tool_confirmation(
        mixin,
        store=store,
        token="token-1",
        tool_call_id="tool-1",
        task_id="task-1",
        timeout=1.0,
        runtime=runtime,
    )

    assert approved is True
    runtime.push.assert_called_once_with(command)
    store.acknowledge.assert_not_awaited()


@pytest.mark.asyncio
async def test_actor_steer_is_durable_and_wakes_conversation_worker():
    task = _task(actor=True)
    control_tasks = SimpleNamespace(
        running=task,
        paused=None,
        to_router_state=lambda: {},
    )
    store = AsyncMock()
    store.append.return_value = {"outcome": "enqueued"}
    wakeup = MagicMock()
    wakeup.publish = AsyncMock()

    with patch.object(ws, "get_db", return_value=MagicMock()), \
         patch.object(ws, "get_async_db", new=AsyncMock(return_value=MagicMock())), \
         patch.object(ws, "load_control_tasks", return_value=control_tasks), \
         patch.object(
             ws.ConversationControlRouter,
             "route",
             new_callable=AsyncMock,
             return_value=SimpleNamespace(action=ws.ControlAction.NONE),
         ), \
         patch.object(ws, "DatabaseConversationCommandStore", return_value=store), \
         patch(
             "services.conversation_worker.RedisConversationWakeup",
             return_value=wakeup,
         ):
        await ws._handle_message(
            "conn-1",
            "user-1",
            {
                "type": "user_steer",
                "payload": {
                    "task_id": "task-1",
                    "conversation_id": "conversation-1",
                    "message": "请改为简短回答",
                },
            },
            org_id="org-1",
        )

    store.append.assert_awaited_once()
    kwargs = store.append.await_args.kwargs
    assert kwargs["command_type"] is CommandType.STEER
    assert kwargs["payload"] == {"message": "请改为简短回答"}
    wakeup.publish.assert_awaited_once_with("conversation-1", "org-1")
