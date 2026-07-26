"""Action Executor SPI。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt, ActionResult


class ExecutionOutcome(StrEnum):
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExecutionReceipt:
    outcome: ExecutionOutcome
    request_hash: str
    external_receipt: Mapping[str, object] = field(default_factory=dict)
    ambiguity_evidence: Mapping[str, object] = field(default_factory=dict)
    result: ActionResult | None = None


class ExecutorPort(Protocol):
    """专业 Executor 的统一外层协议。"""

    async def dispatch(
        self,
        attempt: ActionAttempt,
        request: Mapping[str, object],
    ) -> ExecutionReceipt:
        """执行或提交 Action。"""

    async def reconcile(self, attempt: ActionAttempt) -> ExecutionReceipt:
        """查询 accepted/unknown 外部动作，禁止重复 dispatch。"""

    async def cancel(self, attempt: ActionAttempt) -> ExecutionReceipt:
        """请求取消并返回可证明的结果。"""
