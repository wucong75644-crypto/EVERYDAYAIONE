"""Typed boundary for Runtime-owned scheduled finalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ScheduledTerminalStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScheduledFinalizationOutcome(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"


@dataclass(frozen=True, kw_only=True)
class ScheduledFinalizationClaim:
    scheduled_run_id: str
    claim_token: str
    intent_state_version: int
    claim_lease_expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class ScheduledFinalizationContext:
    scheduled_run_id: str
    terminal_status: ScheduledTerminalStatus
    terminal_baseline: datetime
    intent_state_version: int
    task_state_version: int
    schedule_hash: str
    schedule_type: str
    cron_expr: str | None
    timezone: str
    retry_count: int
    consecutive_failures: int


@dataclass(frozen=True, kw_only=True)
class ScheduledFinalizationProjection:
    request_id: str
    next_run_at: datetime | None
    reason: str = "runtime_finalizer"


@dataclass(frozen=True, kw_only=True)
class ScheduledFinalizationReceipt:
    outcome: ScheduledFinalizationOutcome
    scheduled_run_id: str
    scheduled_task_id: str
    terminal_status: ScheduledTerminalStatus
    scheduled_run_status: str
    task_status: str
    task_state_version: int


class ScheduledFinalizationRepositoryPort(Protocol):
    async def claim_next(
        self, worker_id: str, *, lease_seconds: int = 90,
    ) -> ScheduledFinalizationClaim | None: ...

    async def read_context(
        self, claim: ScheduledFinalizationClaim,
    ) -> ScheduledFinalizationContext: ...

    async def apply(
        self, claim: ScheduledFinalizationClaim,
        context: ScheduledFinalizationContext,
        projection: ScheduledFinalizationProjection,
    ) -> ScheduledFinalizationReceipt: ...
