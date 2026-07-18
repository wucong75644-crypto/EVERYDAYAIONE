"""Evidence Guard 的稳定输入输出协议。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class GuardDecision(StrEnum):
    PASS = "pass"
    RETRY = "retry"
    BLOCK = "block"
    SKIP = "skip"


@dataclass(frozen=True)
class NumericClaim:
    """从最终回答中提取的数值声明，不包含业务领域假设。"""

    raw: str
    value: Decimal
    unit: str | None
    context: str
    start: int
    end: int


@dataclass(frozen=True)
class ClaimIssue:
    claim: NumericClaim
    reason: str


@dataclass(frozen=True)
class GuardReceipt:
    decision: GuardDecision
    claims: tuple[NumericClaim, ...] = ()
    issues: tuple[ClaimIssue, ...] = ()
    evidence_count: int = 0
    reason: str = ""
