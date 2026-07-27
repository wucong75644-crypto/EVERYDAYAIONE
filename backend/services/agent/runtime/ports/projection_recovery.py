"""Controlled Projection dead-stream inspection and recovery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol


@dataclass(frozen=True, kw_only=True)
class ProjectionDeadRecoveryReceipt:
    outcome: str
    outbox_id: str
    audit_id: str
    recovery_version: int
    recovery_count: int
    attempt_count: int
    next_attempt_at: datetime


class ProjectionDeadRecoveryPort(Protocol):
    async def list_dead(
        self, *, limit: int = 50,
    ) -> tuple[Mapping[str, object], ...]: ...

    async def get_dead(
        self, *, outbox_id: str,
    ) -> Mapping[str, object] | None: ...

    async def requeue(
        self, *, outbox_id: str,
        expected_recovery_version: int,
        expected_attempt_count: int,
        recovery_request_id: str,
        reason: str,
        not_before: datetime,
    ) -> ProjectionDeadRecoveryReceipt: ...
