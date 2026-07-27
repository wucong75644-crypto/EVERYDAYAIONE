"""Runtime Projection SPI。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.domain import EventSequence, RuntimeEvent


class ProjectionOutcome(StrEnum):
    DELIVERED = "delivered"
    RETRY = "retry"
    DEAD = "dead"


@dataclass(frozen=True)
class ProjectionReceipt:
    outcome: ProjectionOutcome
    through_sequence: EventSequence
    error_code: str | None = None


@dataclass(frozen=True)
class ProjectionClaim:
    outbox_id: str
    projection_kind: str
    lease_token: str
    lease_expires_at: datetime
    attempt_count: int
    checkpoint: Mapping[str, object]
    event: RuntimeEvent


class ProjectionOutboxPort(Protocol):
    """PostgreSQL Projection Outbox 的 claim/fencing 边界。"""

    async def claim(
        self, batch_size: int = 50, lease_seconds: int = 60,
    ) -> tuple[ProjectionClaim, ...]:
        """认领并返回完整 RuntimeEvent envelope。"""

    async def complete(
        self, claim: ProjectionClaim, checkpoint: Mapping[str, object],
    ) -> None:
        """以 lease token 完成投影。"""

    async def fail(self, claim: ProjectionClaim, error_code: str) -> None:
        """以 lease token 记录失败并释放或终结 outbox。"""


class ProjectionPort(Protocol):
    """Projection 消费事件但不能反向决定业务终态。"""

    async def project(self, event: RuntimeEvent) -> ProjectionReceipt:
        """幂等投影一个 RuntimeEvent。"""
