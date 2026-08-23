"""Conversation Actor Command Inbox 与安全点协议测试。"""

from __future__ import annotations

import asyncio

import pytest

from services.conversation_commands import (
    CommandInbox,
    CommandType,
    ConversationCommand,
    SafePoint,
)
from services.conversation_state import (
    ConversationState,
    ConversationStopRequested,
)
from services.conversation_turn_runtime import ConversationTurnRuntime


def _command(
    command_id: str,
    command_type: CommandType,
) -> ConversationCommand:
    return ConversationCommand(
        command_id=command_id,
        command_type=command_type,
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
    )


def test_command_inbox_deduplicates_and_prioritizes_control_commands():
    inbox = CommandInbox()

    assert inbox.push(_command("tool", CommandType.TOOL_COMPLETED)) is True
    assert inbox.push(_command("cancel", CommandType.CANCEL)) is True
    assert inbox.push(_command("tool", CommandType.TOOL_COMPLETED)) is False

    commands = inbox.drain()

    assert [command.command_type for command in commands] == [
        CommandType.CANCEL,
        CommandType.TOOL_COMPLETED,
    ]
    assert not inbox


@pytest.mark.asyncio
async def test_runtime_applies_tool_completion_at_safe_point():
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
    )
    runtime.push(_command("tool", CommandType.TOOL_COMPLETED))

    await runtime.safe_point(SafePoint.AFTER_TOOL)

    assert runtime.state is ConversationState.RUNNING_MODEL
    assert [command.command_id for command in runtime.applied_commands] == [
        "turn:task-1:turn-1",
        "tool",
    ]


@pytest.mark.asyncio
async def test_runtime_only_stops_cancel_at_safe_point():
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
    )
    runtime.push(_command("cancel", CommandType.CANCEL))

    assert runtime.state is ConversationState.CLAIMED

    with pytest.raises(ConversationStopRequested):
        await runtime.safe_point(SafePoint.AFTER_TOOL)

    assert runtime.state is ConversationState.CANCELLING


@pytest.mark.asyncio
async def test_runtime_pauses_at_safe_point_after_flushing_checkpoint():
    flushed = []

    async def checkpoint():
        flushed.append(True)

    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        checkpoint_callback=checkpoint,
    )
    runtime.push(_command("pause", CommandType.PAUSE))

    with pytest.raises(Exception) as error:
        await runtime.safe_point(SafePoint.AFTER_MODEL)

    assert error.type.__name__ == "ConversationPauseRequested"
    assert runtime.state is ConversationState.PAUSING
    assert flushed == [True]


@pytest.mark.asyncio
async def test_runtime_writes_replay_checkpoint_only_at_replay_boundaries():
    checkpoints = []

    async def replay(point, payload):
        checkpoints.append((point, payload))
        return {"outcome": "written"}

    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        replay_checkpoint_callback=replay,
    )

    await runtime.safe_point(
        SafePoint.BEFORE_MODEL,
        replay_payload={"messages": []},
    )
    await runtime.safe_point(
        SafePoint.MODEL_CHUNK,
        replay_payload={"messages": ["must not write per token"]},
    )
    await runtime.safe_point(
        SafePoint.AFTER_TOOL,
        replay_payload={"messages": [{"role": "tool"}]},
    )

    assert [point for point, _payload in checkpoints] == [
        SafePoint.BEFORE_MODEL,
        SafePoint.AFTER_TOOL,
    ]


@pytest.mark.asyncio
async def test_pause_replay_checkpoint_precedes_delivery_progress_flush():
    order = []

    async def replay(_point, _payload):
        order.append("replay")
        return {"outcome": "written"}

    async def delivery_progress():
        order.append("delivery")

    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        checkpoint_callback=delivery_progress,
        replay_checkpoint_callback=replay,
    )
    runtime.push(_command("pause-before-tool", CommandType.PAUSE))

    with pytest.raises(Exception) as error:
        await runtime.safe_point(
            SafePoint.AFTER_TOOL,
            replay_payload={"messages": [{"role": "tool"}]},
        )

    assert error.type.__name__ == "ConversationPauseRequested"
    assert order == ["replay", "delivery"]


@pytest.mark.asyncio
async def test_runtime_scope_checks_commands():
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
    )
    foreign = ConversationCommand(
        command_id="foreign",
        command_type=CommandType.CANCEL,
        conversation_id="conversation-2",
        task_id="task-1",
    )

    with pytest.raises(ValueError, match="conversation scope"):
        runtime.push(foreign)


@pytest.mark.asyncio
async def test_runtime_cancellation_signal_becomes_ownership_loss():
    cancellation_event = asyncio.Event()
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=cancellation_event,
    )
    cancellation_event.set()

    with pytest.raises(ConversationStopRequested):
        await runtime.safe_point(SafePoint.MODEL_CHUNK)

    assert runtime.state is ConversationState.OWNERSHIP_LOST


class _Store:
    def __init__(self, command: ConversationCommand) -> None:
        self.command = command
        self.acks: list[str] = []

    async def load_pending(self, *, task_id: str, execution_token: str):
        assert task_id == "task-1"
        assert execution_token == "token-1"
        command, self.command = self.command, None
        return [command] if command else []

    async def acknowledge(
        self,
        *,
        event_id: str,
        task_id: str,
        execution_token: str,
        outcome: str = "applied",
    ) -> None:
        self.acks.append(event_id)


@pytest.mark.asyncio
async def test_runtime_loads_and_acknowledges_durable_command():
    command = ConversationCommand(
        command_id="event-1",
        event_id="event-1",
        command_type=CommandType.SUBTASK_COMPLETED,
        conversation_id="conversation-1",
        task_id="task-1",
        payload={
            "child_task_id": "child-1",
            "status": "completed",
            "result": {},
        },
    )
    store = _Store(command)
    runtime: ConversationTurnRuntime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
        command_store=store,
    )

    await runtime.safe_point(SafePoint.AFTER_SUBTASK_COMPLETE)

    assert runtime.state is ConversationState.RUNNING_MODEL
    assert store.acks == ["event-1"]


@pytest.mark.asyncio
async def test_runtime_watcher_delivers_command_without_waiting_for_model_chunk():
    command = _command("pause-event", CommandType.PAUSE)
    store = _Store(command)
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
        command_store=store,
    )

    runtime.start_command_watcher(interval_seconds=0.01)
    try:
        # 构造函数中的 USER_TURN 也会唤醒 event；等待 watcher 的持久命令。
        await asyncio.sleep(0.03)
        with pytest.raises(Exception) as error:
            await runtime.safe_point(SafePoint.MODEL_CHUNK)
        assert error.type.__name__ == "ConversationPauseRequested"
    finally:
        await runtime.stop_command_watcher()


@pytest.mark.asyncio
async def test_runtime_does_not_ack_cancel_before_stopping():
    command = ConversationCommand(
        command_id="event-cancel",
        event_id="event-cancel",
        command_type=CommandType.CANCEL,
        conversation_id="conversation-1",
        task_id="task-1",
    )
    store = _Store(command)
    runtime = ConversationTurnRuntime(
        conversation_id="conversation-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
        command_store=store,
    )

    with pytest.raises(ConversationStopRequested):
        await runtime.safe_point(SafePoint.BEFORE_MODEL)

    assert store.acks == []
