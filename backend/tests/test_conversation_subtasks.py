"""父 Runtime 注册子任务的适配器测试。"""

from types import SimpleNamespace

import pytest

from services.conversation_subtasks import DatabaseConversationSubtaskStore
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.conversation_state import ConversationState
import asyncio


class _RpcCall:
    async def execute(self):
        return SimpleNamespace(data={"outcome": "registered"})


class _DB:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RpcCall()


@pytest.mark.asyncio
async def test_subtask_store_registers_with_parent_fencing_token():
    db = _DB()
    store = DatabaseConversationSubtaskStore(db)

    result = await store.register(
        parent_task_id="parent-1",
        parent_execution_token="token-1",
        parent_command_id="command-1",
        child_task_id="child-1",
    )

    assert result["outcome"] == "registered"
    assert db.calls[0][0] == "register_conversation_subtask"
    assert db.calls[0][1]["p_parent_execution_token"] == "token-1"


@pytest.mark.asyncio
async def test_runtime_register_subtask_enters_waiting_state():
    store = DatabaseConversationSubtaskStore(_DB())
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="parent-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
        subtask_store=store,
    )

    result = await runtime.register_subtask(
        child_task_id="child-1",
        parent_command_id="command-1",
    )

    assert result["outcome"] == "registered"
    assert runtime.state is ConversationState.WAITING_SUBTASK
