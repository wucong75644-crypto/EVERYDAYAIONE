"""一次 Conversation claim 的命令处理与安全点 Runtime。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Mapping

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


ReplayCheckpointCallback = Callable[
    [SafePoint, Mapping[str, Any]], Awaitable[dict[str, Any]]
]


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
        checkpoint_callback: Callable[[], Awaitable[None]] | None = None,
        replay_checkpoint_callback: ReplayCheckpointCallback | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.turn_id = turn_id
        self.cancellation_event = cancellation_event
        self.execution_token = execution_token
        self.command_store = command_store
        self.subtask_store = subtask_store
        self._checkpoint_callback = checkpoint_callback
        self._replay_checkpoint_callback = replay_checkpoint_callback
        self.inbox = CommandInbox()
        self.state = ConversationState.CLAIMED
        self.last_safe_point: SafePoint | None = None
        self.applied_commands: list[ConversationCommand] = []
        self._subtask_completions: list[dict[str, Any]] = []
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

    def set_checkpoint_callback(
        self,
        callback: Callable[[], Awaitable[None]] | None,
    ) -> None:
        """注册安全点前刷新的回调；不负责定义 ReplayCheckpoint。"""
        self._checkpoint_callback = callback

    async def safe_point(
        self,
        point: SafePoint,
        *,
        replay_payload: Mapping[str, Any] | None = None,
    ) -> None:
        """在执行边界归约命令；终止命令只在这里改变控制流。"""
        self.last_safe_point = point
        if self.cancellation_event.is_set():
            self.state = ConversationState.OWNERSHIP_LOST
            raise ConversationStopRequested("ownership_lost")

        if (
            replay_payload is not None
            and self._replay_checkpoint_callback is not None
            and point in {
                SafePoint.BEFORE_MODEL,
                SafePoint.AFTER_TOOL,
                SafePoint.BEFORE_COMMIT,
            }
        ):
            result = await self._replay_checkpoint_callback(
                point, replay_payload,
            )
            if result.get("outcome") in {
                "ownership_lost", "lease_expired", "terminal",
            }:
                self.state = ConversationState.OWNERSHIP_LOST
                raise ConversationStopRequested("ownership_lost")

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
            if command.command_type.value == "subtask_completed":
                self._record_subtask_completion(command)
            self.state = reduce_command(self.state, command)
            if self.state in {
                ConversationState.CANCELLING,
                ConversationState.PAUSING,
                ConversationState.OWNERSHIP_LOST,
            }:
                if self._checkpoint_callback is not None:
                    await self._checkpoint_callback()
                if self.state is ConversationState.PAUSING:
                    raise ConversationPauseRequested
                if self.state is ConversationState.CANCELLING:
                    raise ConversationStopRequested("cancel")
                raise ConversationStopRequested("ownership_lost")
            if command.event_id and self.command_store and self.execution_token:
                await self.command_store.acknowledge(
                    event_id=command.event_id,
                    task_id=self.task_id,
                    execution_token=self.execution_token,
                )

    def _record_subtask_completion(
        self, command: ConversationCommand,
    ) -> None:
        """保存一次已通过 fencing/去重的子任务完成回传。"""
        payload = command.payload or {}
        child_task_id = payload.get("child_task_id")
        status = payload.get("status")
        result = payload.get("result")
        if (
            not isinstance(child_task_id, str)
            or not child_task_id
            or status not in {"completed", "failed", "cancelled"}
            or not isinstance(result, dict)
        ):
            raise RuntimeError("ACTOR_SUBTASK_COMPLETION_PAYLOAD_INVALID")
        self._subtask_completions.append({
            "child_task_id": child_task_id,
            "parent_command_id": payload.get("parent_command_id"),
            "status": status,
            "result": result,
            "error_message": str(payload.get("error_message") or ""),
        })

    def consume_subtask_completions(self) -> list[dict[str, Any]]:
        """取出已在安全点归约的结果；每个完成事件只注入一次。"""
        completions = self._subtask_completions
        self._subtask_completions = []
        return completions

    def set_state(self, state: ConversationState) -> None:
        """记录执行器边界状态；终态不能被普通状态覆盖。"""
        if self.state in {
            ConversationState.COMPLETED,
            ConversationState.FAILED,
            ConversationState.CANCELLED,
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
