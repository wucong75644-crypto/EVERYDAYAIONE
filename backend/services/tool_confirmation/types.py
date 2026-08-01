"""Tool Confirmation V3 value contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class ConfirmationState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    EXECUTION_CLAIMED = "EXECUTION_CLAIMED"


class ConfirmationOutcome(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ConfirmationBinding:
    action_id: str
    interaction_id: str
    interaction_version: int
    task_id: str
    tool_call_id: str
    tool_name: str
    arguments_hash: str
    user_id: str
    org_id: str
    expires_at: datetime


@dataclass(frozen=True)
class ConfirmationRequest:
    confirmation_id: str
    waiter_token: str
    binding: ConfirmationBinding
    summary: Mapping[str, Any]
    safety_level: str


@dataclass(frozen=True)
class ConfirmationDecision:
    outcome: ConfirmationOutcome
    code: str

    @property
    def can_execute(self) -> bool:
        return self.outcome == ConfirmationOutcome.APPROVED
