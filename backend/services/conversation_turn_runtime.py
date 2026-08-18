"""一次 Conversation claim 的命令处理与安全点 Runtime。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from services.conversation_commands import (
    CommandInbox,
    CommandType,
    ConversationCommand,
    SafePoint,
)
from services.conversation_state import (
    ConversationState,
    ConversationPauseRequested,
    ConversationStopRequested,
    reduce_command,
)
from services.conversation_command_store import ConversationCommandStore
from services.conversation_subtasks import ConversationSubtaskStore


class ConversationTurnRuntime:
    """不拥有数据库执行权，只负责当前执行尝试的状态转换。"""

    def __init__(
        self,
        *,
        conversation_id: str,
        task_id: str,
        turn_id: str,
        cancellation_event: asyncio.Event,
        execution_token: str | None = None,
        command_store: ConversationCommandStore | None = None,
        subtask_store: ConversationSubtaskStore | None = None,
        checkpoint: Callable[[], Awaitable[int | None]] | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.turn_id = turn_id
        self.cancellation_event = cancellation_event
        self.execution_token = execution_token
        self.command_store = command_store
        self.subtask_store = subtask_store
        self._checkpoint = checkpoint
        self._command_applier: Callable[[ConversationCommand], Awaitable[None]] | None = None
        self.inbox = CommandInbox()
        self.state = ConversationState.CLAIMED
        self.last_safe_point: SafePoint | None = None
        self.applied_commands: list[ConversationCommand] = []
        self.push(
            ConversationCommand(
                command_id=f"turn:{task_id}:{turn_id}",
                command_type=CommandType.USER_TURN,
                conversation_id=conversation_id,
                task_id=task_id,
                turn_id=turn_id,
            )
        )

    def push(self, command: ConversationCommand) -> bool:
        """向当前执行尝试投递命令。"""
        if command.conversation_id != self.conversation_id:
            raise ValueError("command conversation scope mismatch")
        if command.task_id != self.task_id:
            raise ValueError("command task scope mismatch")
        return self.inbox.push(command)

    def set_checkpoint(self, checkpoint: Callable[[], Awaitable[int | None]] | None) -> None:
        """注册取消安全点使用的最新进度快照回调。"""
        self._checkpoint = checkpoint

    def set_command_applier(
        self,
        applier: Callable[[ConversationCommand], Awaitable[None]] | None,
    ) -> None:
        """注册命令副作用应用器；确认事件前必须先完成应用。"""
        self._command_applier = applier

    async def safe_point(self, point: SafePoint) -> None:
        """在执行边界归约命令；终止命令只在这里改变控制流。"""
        self.last_safe_point = point
        if self.cancellation_event.is_set():
            self.state = ConversationState.OWNERSHIP_LOST
            raise ConversationStopRequested

        # MODEL_CHUNK 每个 token 都会经过，不能在这里访问数据库。
        if (
            point is not SafePoint.MODEL_CHUNK
            and self.command_store
            and self.execution_token
        ):
            commands = await self.command_store.load_pending(
                task_id=self.task_id,
                execution_token=self.execution_token,
            )
            for command in commands:
                self.push(command)

        for command in self.inbox.drain():
            self.applied_commands.append(command)
            self.state = reduce_command(self.state, command)
            if self.state in {
                ConversationState.CANCELLING,
                ConversationState.PAUSING,
                ConversationState.OWNERSHIP_LOST,
            }:
                if (
                    self.state is ConversationState.CANCELLING
                    and command.command_type is CommandType.CANCEL
                    and self._checkpoint is not None
                ):
                    await self._checkpoint()
                if (
                    self.state is ConversationState.PAUSING
                    and command.command_type is CommandType.PAUSE
                    and self._checkpoint is not None
                ):
                    await self._checkpoint()
                    self.state = ConversationState.PAUSED
                    raise ConversationPauseRequested
                if self.state is ConversationState.PAUSING:
                    raise ConversationPauseRequested
                raise ConversationStopRequested
            if (
                self._command_applier is not None
                and command.command_type is not CommandType.USER_TURN
            ):
                await self._command_applier(command)
            if command.event_id and self.command_store and self.execution_token:
                await self.command_store.acknowledge(
                    event_id=command.event_id,
                    task_id=self.task_id,
                    execution_token=self.execution_token,
                )

    def set_state(self, state: ConversationState) -> None:
        """记录执行器边界状态；终态不能被普通状态覆盖。"""
        if self.state in {
            ConversationState.COMPLETED,
            ConversationState.FAILED,
            ConversationState.CANCELLED,
            ConversationState.PAUSED,
            ConversationState.OWNERSHIP_LOST,
        }:
            return
        self.state = state

    async def register_subtask(
        self,
        *,
        child_task_id: str,
        parent_command_id: str,
    ) -> dict[str, object]:
        """登记子任务并进入等待态；子任务结果仍由安全点命令归约。"""
        if self.subtask_store is None or not self.execution_token:
            raise RuntimeError("ACTOR_SUBTASK_STORE_UNAVAILABLE")
        self.set_state(ConversationState.WAITING_SUBTASK)
        return await self.subtask_store.register(
            parent_task_id=self.task_id,
            parent_execution_token=self.execution_token,
            parent_command_id=parent_command_id,
            child_task_id=child_task_id,
        )
