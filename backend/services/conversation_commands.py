"""Conversation Actor 的统一命令、优先级与安全点协议。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CommandType(str, Enum):
    """会影响当前 Conversation 执行流程的逻辑命令。"""

    USER_TURN = "user_turn"
    STEER = "steer"
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_RESULT = "approval_result"
    SUBTASK_COMPLETED = "subtask_completed"
    LEASE_LOST = "lease_lost"
    SHUTDOWN = "shutdown"


class SafePoint(str, Enum):
    """Runtime 允许处理 Command 的执行边界。"""

    BEFORE_MODEL = "before_model"
    MODEL_CHUNK = "model_chunk"
    AFTER_MODEL = "after_model"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_SUBTASK_WAIT = "before_subtask_wait"
    AFTER_SUBTASK_COMPLETE = "after_subtask_complete"
    BEFORE_COMMIT = "before_commit"


_COMMAND_PRIORITY: dict[CommandType, int] = {
    CommandType.LEASE_LOST: 0,
    CommandType.CANCEL: 1,
    CommandType.PAUSE: 1,
    CommandType.RESUME: 1,
    CommandType.SHUTDOWN: 1,
    CommandType.APPROVAL_RESULT: 2,
    CommandType.SUBTASK_COMPLETED: 2,
    CommandType.TOOL_COMPLETED: 3,
    # User turn 与普通完成事件保持到达顺序；控制命令仍优先。
    CommandType.USER_TURN: 3,
    CommandType.STEER: 2,
}


@dataclass(frozen=True)
class ConversationCommand:
    """一次不可变的 Conversation 控制意图或完成事件。"""

    command_id: str
    command_type: CommandType
    conversation_id: str
    task_id: str
    turn_id: str | None = None
    payload: dict[str, Any] | None = None
    event_id: str | None = None

    def __post_init__(self) -> None:
        if not self.command_id:
            raise ValueError("command_id must be non-empty")
        if not self.conversation_id:
            raise ValueError("conversation_id must be non-empty")
        if not self.task_id:
            raise ValueError("task_id must be non-empty")

    @property
    def priority(self) -> int:
        return _COMMAND_PRIORITY[self.command_type]


class CommandInbox:
    """当前执行尝试的轻量命令 Inbox；持久事实仍由 PostgreSQL 保存。"""

    def __init__(self) -> None:
        self._pending: deque[ConversationCommand] = deque()
        self._seen_ids: set[str] = set()

    def push(self, command: ConversationCommand) -> bool:
        """加入命令；相同 command_id 只接受一次。"""
        if command.command_id in self._seen_ids:
            return False
        self._seen_ids.add(command.command_id)
        self._pending.append(command)
        return True

    def drain(self) -> tuple[ConversationCommand, ...]:
        """按控制优先级取出当前批次，保持同优先级的到达顺序。"""
        commands = list(self._pending)
        self._pending.clear()
        commands.sort(key=lambda command: command.priority)
        return tuple(commands)

    def __bool__(self) -> bool:
        return bool(self._pending)
