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
