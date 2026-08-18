"""Conversation Actor 数据库控制事件适配器测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.conversation_command_store import DatabaseConversationCommandStore
from services.conversation_commands import CommandType


class _RpcCall:
    def __init__(self, data):
        self._data = data

    async def execute(self):
        return SimpleNamespace(data=self._data)


class _DB:
    def __init__(self):
        self.calls = []
        self.responses = {
            "append_conversation_control_command": {
                "outcome": "enqueued",
                "event_id": "event-2",
                "already_enqueued": False,
            },
            "read_conversation_control_commands": [
                {
                    "id": "event-1",
                    "conversation_id": "conversation-1",
                    "task_id": "task-1",
                    "turn_id": "turn-1",
                    "event_type": "subtask_completed",
                    "payload": {"child_task_id": "child-1"},
                    "event_sequence": 1,
                },
            ],
            "acknowledge_conversation_control_command": {
                "outcome": "applied",
            },
        }

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RpcCall(self.responses[name])


@pytest.mark.asyncio
async def test_store_appends_typed_approval_event():
    db = _DB()
    store = DatabaseConversationCommandStore(db)

    result = await store.append(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        command_type=CommandType.APPROVAL_RESULT,
        dedupe_key="approval:tc-1",
        payload={"tool_call_id": "tc-1", "approved": True},
    )

    assert result["event_id"] == "event-2"
    assert db.calls[0][0] == "append_conversation_control_command"
    assert db.calls[0][1]["p_event_type"] == "approval_result"


@pytest.mark.asyncio
async def test_store_appends_durable_cancel_event():
    db = _DB()
    store = DatabaseConversationCommandStore(db)

    await store.append(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        command_type=CommandType.CANCEL,
        dedupe_key="cancel:task-1",
        payload={"reason": "user_cancelled"},
    )

    assert db.calls[0][1]["p_event_type"] == "cancel"


@pytest.mark.asyncio
async def test_store_loads_typed_commands_from_rpc():
    db = _DB()
    store = DatabaseConversationCommandStore(db)

    commands = await store.load_pending(
        task_id="task-1",
        execution_token="token-1",
    )

    assert commands[0].command_type is CommandType.SUBTASK_COMPLETED
    assert commands[0].event_id == "event-1"
    assert commands[0].payload == {"child_task_id": "child-1"}
    assert db.calls[0][1]["p_limit"] == 50


@pytest.mark.asyncio
async def test_store_acknowledges_event_with_fencing_token():
    db = _DB()
    store = DatabaseConversationCommandStore(db)

    await store.acknowledge(
        event_id="event-1",
        task_id="task-1",
        execution_token="token-1",
    )

    assert db.calls[0] == (
        "acknowledge_conversation_control_command",
        {
            "p_event_id": "event-1",
            "p_task_id": "task-1",
            "p_execution_token": "token-1",
            "p_outcome": "applied",
        },
    )
