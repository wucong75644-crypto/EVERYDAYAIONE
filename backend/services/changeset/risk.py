"""风险分级接口与默认策略。

默认策略只决定风险标签和是否需要一次审批，不实现多人审批、审批编排或组织策略平台。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    requires_approval: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    policy_version: str = "default.v1"

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "version": self.policy_version,
            "risk_level": self.level.value,
            "requires_approval": self.requires_approval,
            "reasons": list(self.reasons),
        }


class RiskPolicy(Protocol):
    def assess(
        self,
        *,
        resource_type: str,
        operation: str,
        context: Mapping[str, Any] | None = None,
    ) -> RiskAssessment:
        """返回可冻结到 ChangeSet 的风险/策略快照。"""


class DefaultRiskPolicy:
    """可替换的默认四级风险策略。"""

    def assess(
        self,
        *,
        resource_type: str,
        operation: str,
        context: Mapping[str, Any] | None = None,
    ) -> RiskAssessment:
        del resource_type
        context = context or {}
        reasons: list[str] = []
        normalized_operation = operation.lower().strip()
        if context.get("affects_many"):
            reasons.append("affects_many")
            return RiskAssessment(RiskLevel.CRITICAL, True, tuple(reasons))
        if context.get("destructive"):
            reasons.append("destructive_operation")
            return RiskAssessment(RiskLevel.HIGH, True, tuple(reasons))
        if context.get("external_effect"):
            reasons.append("external_effect")
            return RiskAssessment(RiskLevel.HIGH, True, tuple(reasons))
        if normalized_operation in {"read", "preview", "render"}:
            reasons.append("read_only")
            return RiskAssessment(RiskLevel.LOW, False, tuple(reasons))
        reasons.append("persistent_state_change")
        return RiskAssessment(RiskLevel.MEDIUM, False, tuple(reasons))
