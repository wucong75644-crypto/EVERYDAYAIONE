"""RuntimeEvent 信封与 Session sequence 类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from services.agent.runtime.domain.identity import (
    ActionId,
    ModelStepId,
    RunId,
    RuntimeEventId,
    SessionId,
    require_stable_value,
)
from services.agent.runtime.domain.scope import RuntimeScope


class EventDurability(StrEnum):
    DURABLE = "durable"
    EPHEMERAL_COMPACTED = "ephemeral_compacted"


class RuntimeActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    MODEL = "model"
    EXECUTOR = "executor"
    RECONCILER = "reconciler"
    ADMIN = "admin"


@dataclass(frozen=True, order=True)
class EventSequence:
    """单 Session 内严格递增的正整数 sequence。"""

    value: int

    def __post_init__(self) -> None:
        if self.value < 1:
            raise ValueError("event sequence must be positive")


@dataclass(frozen=True, kw_only=True)
class RuntimeEventDraft:
    """尚未由 Event Store 分配 ID 与 sequence 的事件内容。"""

    session_id: SessionId
    scope: RuntimeScope
    event_type: str
    event_version: int
    durability: EventDurability
    correlation_id: str
    actor_type: RuntimeActorType
    payload_hash: str
    occurred_at: datetime
    redaction_revision: str
    run_id: RunId | None = None
    model_step_id: ModelStepId | None = None
    action_id: ActionId | None = None
    actor_id: str | None = None
    causation_event_id: RuntimeEventId | None = None
    payload: Mapping[str, object] = field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.session_id, "session_id"),
            (self.event_type, "event_type"),
            (self.correlation_id, "correlation_id"),
            (self.payload_hash, "payload_hash"),
            (self.redaction_revision, "redaction_revision"),
        ):
            require_stable_value(value, name)
        if self.event_version < 1:
            raise ValueError("event_version must be positive")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")


@dataclass(frozen=True, kw_only=True)
class RuntimeEvent(RuntimeEventDraft):
    """与状态变更同事务追加的领域事件信封。"""

    event_id: RuntimeEventId
    sequence: EventSequence

    def __post_init__(self) -> None:
        super().__post_init__()
        require_stable_value(self.event_id, "event_id")
