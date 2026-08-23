"""父 Runtime 注册子任务的适配器测试。"""

from types import SimpleNamespace

import pytest

from services.conversation_subtasks import DatabaseConversationSubtaskStore
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.conversation_state import ConversationState
from services.conversation_commands import (
    CommandType,
    ConversationCommand,
    SafePoint,
)
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


@pytest.mark.asyncio
async def test_runtime_consumes_subtask_completion_once_at_safe_point():
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="parent-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
    )
    runtime.push(ConversationCommand(
        command_id="subtask-event-1",
        command_type=CommandType.SUBTASK_COMPLETED,
        conversation_id="conversation-1",
        task_id="parent-1",
        payload={
            "child_task_id": "child-1",
            "status": "completed",
            "result": {"answer": "42"},
        },
    ))

    await runtime.safe_point(SafePoint.AFTER_SUBTASK_COMPLETE)

    assert runtime.state is ConversationState.RUNNING_MODEL
    assert runtime.consume_subtask_completions() == [{
        "child_task_id": "child-1",
        "parent_command_id": None,
        "status": "completed",
        "result": {"answer": "42"},
        "error_message": "",
    }]
    assert runtime.consume_subtask_completions() == []


@pytest.mark.asyncio
async def test_runtime_rejects_invalid_subtask_completion_payload():
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="parent-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
    )
    runtime.push(ConversationCommand(
        command_id="subtask-event-invalid",
        command_type=CommandType.SUBTASK_COMPLETED,
        conversation_id="conversation-1",
        task_id="parent-1",
        payload={"child_task_id": "child-1", "status": "completed"},
    ))

    with pytest.raises(RuntimeError, match="SUBTASK_COMPLETION_PAYLOAD_INVALID"):
        await runtime.safe_point(SafePoint.AFTER_SUBTASK_COMPLETE)
