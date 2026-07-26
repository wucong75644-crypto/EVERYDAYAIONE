"""Run 状态与执行 Attempt 合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from services.agent.runtime.domain.execution import Lease, require_aware_datetime
from services.agent.runtime.domain.identity import RunId, require_stable_value
from services.agent.runtime.domain.scope import RuntimeScope


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_ACTIONS = "waiting_actions"
    WAITING_INTERACTION = "waiting_interaction"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunAttemptOutcome(StrEnum):
    COMPLETED = "completed"
    LEASE_LOST = "lease_lost"
    CRASHED = "crashed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class RunAttempt:
    """一次 Run claim；业务状态与执行权保持分离。"""

    run_id: RunId
    scope: RuntimeScope
    attempt_number: int
    worker_id: str
    lease: Lease
    claimed_at: datetime
    ended_at: datetime | None = None
    outcome: RunAttemptOutcome | None = None
    error_class: str | None = None

    def __post_init__(self) -> None:
        require_stable_value(self.run_id, "run_id")
        require_stable_value(self.worker_id, "worker_id")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        require_aware_datetime(self.claimed_at, "claimed_at")
        require_aware_datetime(self.ended_at, "ended_at")
        if (self.ended_at is None) != (self.outcome is None):
            raise ValueError("ended_at and outcome must be set together")
