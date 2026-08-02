"""Action Executor SPI。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt, ActionResult
from services.agent.runtime.domain.identity import require_stable_value
if TYPE_CHECKING:
    from services.agent.runtime.executors.specialist_contracts import ReconciliationContext


class ExecutionOutcome(StrEnum):
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutorDispatchUnknown(RuntimeError):
    """A submit may have committed; callers must persist reconcile facts."""

    def __init__(self, evidence: Mapping[str, object]) -> None:
        super().__init__("EXECUTOR_DISPATCH_UNKNOWN")
        self.evidence = dict(evidence)


@dataclass(frozen=True)
class ExecutionReceipt:
    outcome: ExecutionOutcome
    request_hash: str
    external_receipt: Mapping[str, object] = field(default_factory=dict)
    ambiguity_evidence: Mapping[str, object] = field(default_factory=dict)
    result: ActionResult | None = None

    def __post_init__(self) -> None:
        require_stable_value(self.request_hash, "request_hash")
        if (self.outcome is ExecutionOutcome.COMPLETED) != (self.result is not None):
            raise ValueError("completed outcome requires ActionResult exclusively")
        if self.outcome is ExecutionOutcome.ACCEPTED and not self.external_receipt:
            raise ValueError("accepted outcome requires external_receipt")
        if (
            self.outcome is ExecutionOutcome.UNKNOWN
            and not self.ambiguity_evidence
        ):
            raise ValueError("unknown outcome requires ambiguity_evidence")


class ExecutorPort(Protocol):
    """专业 Executor 的统一外层协议。"""

    async def dispatch(
        self,
        attempt: ActionAttempt,
        request: Mapping[str, object],
    ) -> ExecutionReceipt:
        """执行或提交 Action。"""

    async def reconcile(
        self, attempt: ActionAttempt, context: ReconciliationContext | None = None,
    ) -> ExecutionReceipt:
        """查询 accepted/unknown 外部动作，禁止重复 dispatch。"""

    async def cancel(
        self, attempt: ActionAttempt, context: ReconciliationContext | None = None,
    ) -> ExecutionReceipt:
        """请求取消并返回可证明的结果。"""


class DispatchCapabilityIssuerPort(Protocol):
    """Trusted application issuer; Executors cannot mint capabilities."""

    def issue(
        self, *, attempt: ActionAttempt, descriptor: object,
        phase: str, dispatch_gate: object | None = None,
    ) -> Mapping[str, object]: ...
