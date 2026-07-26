"""Runtime Projection SPI。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

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


class ProjectionPort(Protocol):
    """Projection 消费事件但不能反向决定业务终态。"""

    async def project(self, event: RuntimeEvent) -> ProjectionReceipt:
        """幂等投影一个 RuntimeEvent。"""
