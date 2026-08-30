"""Conversation Actor Runtime 的状态与命令归约。"""

from __future__ import annotations

import asyncio
from enum import Enum

from services.conversation_commands import CommandType, ConversationCommand


class ConversationState(str, Enum):
    """单次 claim 执行尝试的内存状态。"""

    CLAIMED = "claimed"
    RUNNING_MODEL = "running_model"
    WAITING_TOOL = "waiting_tool"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_SUBTASK = "waiting_subtask"
    CANCELLING = "cancelling"
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OWNERSHIP_LOST = "ownership_lost"


class ConversationStopRequested(asyncio.CancelledError):
    """Runtime 在安全点确认需要停止当前执行。"""

    def __init__(self, reason: str = "cancel") -> None:
        super().__init__(reason)
        self.reason = reason


class ConversationPauseRequested(RuntimeError):
    """Runtime 在安全点确认需要保存快照并暂停。"""


def reduce_command(
    state: ConversationState,
    command: ConversationCommand,
) -> ConversationState:
    """将命令归约为下一状态，不执行数据库或外部副作用。"""
    if command.command_type == CommandType.LEASE_LOST:
        return ConversationState.OWNERSHIP_LOST
    if command.command_type == CommandType.CANCEL or command.command_type == CommandType.SHUTDOWN:
        return ConversationState.CANCELLING
    if command.command_type == CommandType.PAUSE:
        return ConversationState.PAUSING
    if command.command_type == CommandType.RESUME:
        return ConversationState.RESUMING
    if command.command_type == CommandType.APPROVAL_RESULT:
        return ConversationState.RUNNING_MODEL
    if command.command_type == CommandType.SUBTASK_COMPLETED:
        return ConversationState.RUNNING_MODEL
    if command.command_type == CommandType.TOOL_COMPLETED:
        return ConversationState.RUNNING_MODEL
    if command.command_type == CommandType.STEER:
        return ConversationState.RUNNING_MODEL
    return state
