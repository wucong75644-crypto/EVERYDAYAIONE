"""Session 协调周期状态与命令协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from services.agent.runtime.domain.identity import (
    IdempotencyKey,
    SessionId,
    require_stable_value,
)
from services.agent.runtime.domain.scope import RuntimeScope


class SessionStatus(StrEnum):
    IDLE = "idle"
    CLAIMED = "claimed"
    HYDRATING = "hydrating"
    READY = "ready"
    SAMPLING = "sampling"
    EXECUTING_ACTIONS = "executing_actions"
    COMPACTING = "compacting"
    AUTH_RECOVERY = "auth_recovery"
    WAITING_EXTERNAL = "waiting_external"
    COMMITTING = "committing"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OWNERSHIP_LOST = "ownership_lost"
    FAILED = "failed"


class SessionCommandType(StrEnum):
    SUBMIT_INPUT = "submit_input"
    STEER = "steer"
    CANCEL = "cancel"
    APPROVE = "approve"
    REJECT = "reject"
    SWITCH_AGENT = "switch_agent"
    COMPACT = "compact"


@dataclass(frozen=True)
class SessionCommand:
    """Ingress 规范化后的稳定、可幂等 Session 命令。"""

    command_id: str
    session_id: SessionId
    command_type: SessionCommandType
    idempotency_key: IdempotencyKey
    request_hash: str
    scope: RuntimeScope
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_stable_value(self.command_id, "command_id")
        require_stable_value(self.session_id, "session_id")
        require_stable_value(self.idempotency_key, "idempotency_key")
        require_stable_value(self.request_hash, "request_hash")
