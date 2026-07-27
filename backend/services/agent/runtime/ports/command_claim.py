"""Typed application boundary for durable Session Command coordination."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from services.agent.runtime.domain import FencingToken, RunId, SessionId


class CommandClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    FOUND = "found"
    NOT_FOUND = "not_found"
    ALREADY_CLAIMED = "already_claimed"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    SCOPE_REJECTED = "scope_rejected"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    TERMINAL_CONFLICT = "terminal_conflict"
    RENEWED = "renewed"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class CommandClaim:
    command_id: str
    session_id: SessionId
    run_id: RunId
    worker_id: str
    fencing_token: FencingToken
    lease_expires_at: datetime
    attempt_number: int
    command_type: str


@dataclass(frozen=True)
class CommandClaimReceipt:
    outcome: CommandClaimOutcome
    claim: CommandClaim | None = None


class CommandClaimRepositoryPort(Protocol):
    async def claim_next(
        self, worker_id: str, *, lease_seconds: int = 90,
        max_attempts: int = 3,
    ) -> CommandClaimReceipt:
        """Claim the next PostgreSQL-pending Command and ensure its Run."""

    async def get_claim(
        self, command_id: str, worker_id: str,
    ) -> CommandClaimReceipt:
        """Read back a committed claim after an uncertain response."""

    async def renew(
        self, claim: CommandClaim, *, lease_seconds: int = 90,
    ) -> CommandClaimReceipt:
        """Renew only while the fencing token still owns the claim."""

    async def finish(
        self, claim: CommandClaim, outcome: CommandClaimOutcome,
        *, error_class: str | None = None,
    ) -> CommandClaimReceipt:
        """Persist a completed or failed claim terminal fact."""
